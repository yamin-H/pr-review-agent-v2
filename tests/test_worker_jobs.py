from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from review.db.models import ReviewRun
from review.db.session import create_engine, drop_db, get_session_maker, init_db
from review.findings import Finding, ReviewOutput, Severity
from review.worker.jobs import (
    generate_idempotency_key,
    get_or_create_repo_record,
    process_review_job,
)


@pytest_asyncio.fixture
async def worker_test_db(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Set up in-memory SQLite database and monkeypatch session getter."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    maker = get_session_maker(engine)

    @asynccontextmanager
    async def mock_get_async_session() -> AsyncGenerator[AsyncSession, None]:
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    import review.worker.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "get_async_session", mock_get_async_session)

    yield maker

    await drop_db(engine)
    await engine.dispose()


def test_generate_idempotency_key() -> None:
    key = generate_idempotency_key("Pallets", "Flask", 42, "abcdef123456")
    assert key == "review:pallets/flask:42:abcdef123456"


@pytest.mark.asyncio
async def test_get_or_create_repo_record_creates_tenant_and_repo(
    worker_test_db: async_sessionmaker[AsyncSession],
) -> None:
    maker = worker_test_db
    async with maker() as session:
        repo = await get_or_create_repo_record(session, "my-org", "my-repo", 12345)
        await session.commit()
        assert repo.id is not None
        assert repo.full_name == "my-org/my-repo"
        assert repo.tenant_id is not None

    # Idempotent second call returns existing repo
    async with maker() as session:
        repo2 = await get_or_create_repo_record(session, "my-org", "my-repo", 12345)
        assert repo2.id == repo.id


@pytest.mark.asyncio
async def test_process_review_job_skips_duplicate_success(
    worker_test_db: async_sessionmaker[AsyncSession],
) -> None:
    maker = worker_test_db
    idempotency_key = generate_idempotency_key("org", "repo", 1, "sha123")

    async with maker() as session:
        repo = await get_or_create_repo_record(session, "org", "repo", 100)
        existing = ReviewRun(
            repository_id=repo.id,
            pull_number=1,
            head_sha="sha123",
            idempotency_key=idempotency_key,
            status="success",
        )
        session.add(existing)
        await session.commit()

    payload = {
        "owner": "org",
        "repo": "repo",
        "pull_number": 1,
        "head_sha": "sha123",
        "installation_id": 100,
    }

    result = await process_review_job(None, payload)
    assert result["status"] == "skipped"
    assert "duplicate_success" in result["reason"]


@pytest.mark.asyncio
async def test_process_review_job_empty_diff(
    worker_test_db: async_sessionmaker[AsyncSession],
) -> None:
    payload = {
        "owner": "empty-org",
        "repo": "empty-repo",
        "pull_number": 5,
        "head_sha": "sha_empty",
        "installation_id": 999,
    }

    with (
        patch("review.worker.jobs.GitHubAppAuth") as mock_auth,
        patch("review.worker.jobs.GitHubClient") as mock_client,
    ):
        mock_auth.return_value.get_installation_token.return_value = "fake_token"
        mock_client.return_value.get_pull_request_diff.return_value = ""

        result = await process_review_job(None, payload)
        assert result["status"] == "empty_diff"

    # Verify status in database
    maker = worker_test_db
    async with maker() as session:
        run_res = await session.execute(
            select(ReviewRun).where(ReviewRun.id == result["review_run_id"])
        )
        run = run_res.scalar_one()
        assert run.status == "empty_diff"
        assert run.summary is not None
        assert "No code changes" in run.summary


@pytest.mark.asyncio
async def test_process_review_job_success_with_findings(
    worker_test_db: async_sessionmaker[AsyncSession],
) -> None:
    payload = {
        "owner": "active-org",
        "repo": "active-repo",
        "pull_number": 7,
        "head_sha": "sha_active",
        "installation_id": 555,
        "title": "Add feature",
        "body": "PR description",
    }

    mock_finding = Finding(
        file="app.py",
        line=10,
        title="Sample issue",
        body="Fix this issue",
        severity=Severity.HIGH,
        citation="PR #1",
    )
    mock_review_output = ReviewOutput(
        summary="Review completed successfully.",
        findings=[mock_finding],
    )

    with (
        patch("review.worker.jobs.GitHubAppAuth") as mock_auth,
        patch("review.worker.jobs.GitHubClient") as mock_client,
        patch("review.worker.jobs.AgentRunner") as mock_runner_cls,
        patch("review.worker.jobs.ReviewPublisher") as mock_publisher,
    ):
        mock_auth.return_value.get_installation_token.return_value = "fake_token"
        mock_client.return_value.get_pull_request_diff.return_value = (
            "diff --git a/app.py b/app.py\n..."
        )
        mock_runner_cls.return_value.review_to_output.return_value = mock_review_output
        mock_publisher.return_value.publish_review.return_value = {"id": 1}

        result = await process_review_job(None, payload)
        assert result["status"] == "success"
        assert result["findings_count"] == 1

    # Verify run and finding persisted in database
    maker = worker_test_db
    async with maker() as session:
        run_res = await session.execute(
            select(ReviewRun).where(ReviewRun.id == result["review_run_id"])
        )
        run = run_res.scalar_one()
        assert run.status == "success"
        assert run.summary == "Review completed successfully."
