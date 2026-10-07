"""Asynchronous queue worker jobs for processing pull request reviews with idempotency."""

import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import select

from review.agent.graph import AgentRunner
from review.db.models import ExecutionTrace, FindingRecord, Repository, ReviewRun, Tenant
from review.db.session import get_async_session
from review.github.app_auth import GitHubAppAuth
from review.github.client import GitHubClient
from review.github.comments import ReviewPublisher
from review.llm import GroqReviewer
from review.sandbox.prover import ProofEngine
from review.sandbox.synthesizer import ReproductionSynthesizer
from review.service.config import ServiceConfig
from review.telemetry.registry import get_global_registry
from review.telemetry.tracker import TelemetryTracker
from review.verifier import FindingVerifier

logger = logging.getLogger("review.worker.jobs")


def generate_idempotency_key(owner: str, repo: str, pull_number: int, head_sha: str) -> str:
    """Generate a deterministic deduplication key for a PR review task."""
    return f"review:{owner.lower()}/{repo.lower()}:{pull_number}:{head_sha}"


async def get_or_create_repo_record(
    session: Any, owner: str, repo_name: str, installation_id: int
) -> Repository:
    """Ensure tenant and repository database records exist, creating them if needed."""
    full_name = f"{owner.lower()}/{repo_name.lower()}"

    # 1. Tenant
    result = await session.execute(
        select(Tenant).where(Tenant.github_org_id == owner.lower())
    )
    tenant = result.scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(
            github_org_id=owner.lower(),
            name=owner,
            plan_tier="standard",
        )
        session.add(tenant)
        await session.flush()

    # 2. Repository
    result = await session.execute(
        select(Repository).where(Repository.full_name == full_name)
    )
    repo = result.scalar_one_or_none()
    if repo is None:
        repo = Repository(
            tenant_id=tenant.id,
            full_name=full_name,
            github_repo_id=installation_id,
            is_active=True,
            settings={},
        )
        session.add(repo)
        await session.flush()

    return cast(Repository, repo)


async def process_review_job(
    ctx: dict[str, Any] | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Execute a pull request review task asynchronously with database persistence.

    Enforces idempotency using the key: 'review:{owner}/{repo}:{pull_number}:{head_sha}'.
    """
    owner = payload.get("owner", "")
    repo_name = payload.get("repo", "")
    pull_number = int(payload.get("pull_number", 0))
    head_sha = payload.get("head_sha", "")
    base_sha = payload.get("base_sha")
    installation_id = int(payload.get("installation_id", 0))
    delivery_id = payload.get("delivery_id", "manual")
    title = payload.get("title", "")
    body = payload.get("body", "")

    idempotency_key = generate_idempotency_key(owner, repo_name, pull_number, head_sha)
    logger.info("Processing review job: %s (Delivery: %s)", idempotency_key, delivery_id)

    config = ServiceConfig()
    start_time = time.time()
    tracker = TelemetryTracker(
        repo=f"{owner}/{repo_name}",
        pull_number=pull_number,
        delivery_id=delivery_id,
    )

    # 1. Idempotency Check & Record Initialization
    async with get_async_session() as session:
        repo_record = await get_or_create_repo_record(
            session=session,
            owner=owner,
            repo_name=repo_name,
            installation_id=installation_id,
        )

        existing_run_res = await session.execute(
            select(ReviewRun).where(ReviewRun.idempotency_key == idempotency_key)
        )
        existing_run = existing_run_res.scalar_one_or_none()

        if existing_run and existing_run.status in {"success", "running"}:
            logger.info(
                "Skipping duplicate review run: %s is already %s",
                idempotency_key,
                existing_run.status,
            )
            return {
                "status": "skipped",
                "reason": f"idempotent_duplicate_{existing_run.status}",
                "review_run_id": existing_run.id,
            }

        if existing_run:
            review_run = existing_run
            review_run.status = "running"
            review_run.started_at = datetime.now(UTC)
        else:
            review_run = ReviewRun(
                repository_id=repo_record.id,
                pull_number=pull_number,
                head_sha=head_sha,
                base_sha=base_sha,
                idempotency_key=idempotency_key,
                status="running",
                started_at=datetime.now(UTC),
            )
            session.add(review_run)

        await session.flush()
        review_run_id = review_run.id
        await session.commit()

    # 2. Execution Pipeline
    try:
        auth = GitHubAppAuth(
            app_id=config.app_id,
            private_key_pem=config.get_private_key_pem(),
        )
        token = auth.get_installation_token(installation_id)
        github_client = GitHubClient(token=token)

        diff_text = github_client.get_pull_request_diff(
            owner=owner,
            repo=repo_name,
            pull_number=pull_number,
        )

        if not diff_text.strip():
            logger.info(
                "Empty diff for %s/%s#%d, completing without findings.",
                owner,
                repo_name,
                pull_number,
            )
            elapsed_ms = (time.time() - start_time) * 1000.0
            async with get_async_session() as session:
                run_res = await session.execute(
                    select(ReviewRun).where(ReviewRun.id == review_run_id)
                )
                run = run_res.scalar_one()
                run.status = "empty_diff"
                run.summary = "No code changes detected in pull request."
                run.duration_ms = elapsed_ms
                run.completed_at = datetime.now(UTC)
                await session.commit()

            trace = tracker.finish(status="empty_diff", findings_count=0)
            get_global_registry().record_trace(trace)
            return {"status": "empty_diff", "review_run_id": review_run_id}

        reviewer = GroqReviewer()
        verifier = FindingVerifier(reviewer=reviewer) if config.enable_verifier else None
        proof_engine = (
            ProofEngine(synthesizer=ReproductionSynthesizer(reviewer=reviewer))
            if config.enable_reproduction
            else None
        )

        runner = AgentRunner(
            reviewer=reviewer,
            verifier=verifier,
            proof_engine=proof_engine,
            enable_verifier=config.enable_verifier,
            enable_reproduction=config.enable_reproduction,
        )

        review_output = runner.review_to_output(
            diff_text=diff_text,
            repo_root=Path.cwd(),
            repo=f"{owner}/{repo_name}",
            pr_title=title,
            pr_description=body,
            tracker=tracker,
        )

        # 3. Publish to GitHub
        publisher = ReviewPublisher(github_client=github_client)
        publisher.publish_review(
            owner=owner,
            repo=repo_name,
            pull_number=pull_number,
            head_sha=head_sha,
            review_output=review_output,
            pr_title=title,
        )

        elapsed_ms = (time.time() - start_time) * 1000.0
        trace = tracker.finish(status="success", findings_count=len(review_output.findings))
        get_global_registry().record_trace(trace)

        # 4. Persist Results & Findings to Database
        async with get_async_session() as session:
            run_res = await session.execute(select(ReviewRun).where(ReviewRun.id == review_run_id))
            run = run_res.scalar_one()
            run.status = "success"
            run.summary = review_output.summary
            run.duration_ms = elapsed_ms
            run.total_tokens = trace.total_tokens
            run.cost_usd = trace.estimated_cost_usd
            run.completed_at = datetime.now(UTC)

            for finding in review_output.findings:
                finding_rec = FindingRecord(
                    review_run_id=review_run_id,
                    file_path=finding.file,
                    line_number=finding.line,
                    title=finding.title,
                    body=finding.body,
                    severity=finding.severity.value,
                    status="verified",
                    is_reproduced=finding.is_reproduced,
                    reproduction_code=finding.reproduction_code,
                    reproduction_output=finding.reproduction_output,
                    precedent_citation=finding.citation,
                )
                session.add(finding_rec)

            trace_rec = ExecutionTrace(
                review_run_id=review_run_id,
                trace_id=trace.trace_id,
                prompt_tokens=trace.prompt_tokens,
                completion_tokens=trace.completion_tokens,
                spans=[s.model_dump() for s in trace.spans],
                latency_breakdown=trace.get_latency_breakdown(),
            )
            session.add(trace_rec)
            await session.commit()

        return {
            "status": "success",
            "review_run_id": review_run_id,
            "findings_count": len(review_output.findings),
            "duration_ms": elapsed_ms,
        }

    except Exception as exc:
        logger.error("Error executing review job %s: %s", idempotency_key, exc, exc_info=True)
        elapsed_ms = (time.time() - start_time) * 1000.0
        async with get_async_session() as session:
            run_res = await session.execute(select(ReviewRun).where(ReviewRun.id == review_run_id))
            failed_run = run_res.scalar_one_or_none()
            if failed_run:
                failed_run.status = "failed"
                failed_run.summary = f"Review execution encountered error: {exc}"
                failed_run.duration_ms = elapsed_ms
                failed_run.completed_at = datetime.now(UTC)
                await session.commit()

        trace = tracker.finish(status="failed", findings_count=0)
        get_global_registry().record_trace(trace)
        raise


__all__ = [
    "generate_idempotency_key",
    "get_or_create_repo_record",
    "process_review_job",
]
