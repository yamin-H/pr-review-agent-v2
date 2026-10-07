"""Asynchronous review worker executing background tasks decoupled from webhooks."""

import asyncio
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from review.agent.graph import AgentRunner
from review.findings import ReviewOutput
from review.github.client import GitHubAppAuth, GitHubClient
from review.github.publisher import ReviewPublisher
from review.llm import GroqReviewer
from review.sandbox.prover import ProofEngine
from review.sandbox.synthesizer import ReproductionSynthesizer
from review.service.config import ServiceConfig
from review.verifier import FindingVerifier

logger = logging.getLogger("review.service.worker")


class ReviewTask(BaseModel):
    """Encapsulates all metadata required to process a PR review asynchronously."""

    delivery_id: str = Field(..., description="Unique GitHub webhook delivery ID")
    installation_id: int = Field(..., description="GitHub App installation ID")
    owner: str = Field(..., description="Repository owner / organization")
    repo: str = Field(..., description="Repository name")
    pull_number: int = Field(..., description="Pull request number")
    head_sha: str = Field(..., description="Commit SHA of PR head")
    title: str = Field(default="", description="Pull request title")
    body: str = Field(default="", description="Pull request description")


class BackgroundReviewWorker:
    """Orchestrates async background execution of end-to-end pull request reviews."""

    def __init__(
        self,
        config: ServiceConfig,
        auth: GitHubAppAuth | None = None,
        reviewer: GroqReviewer | None = None,
    ) -> None:
        self.config = config
        self.auth = auth or GitHubAppAuth(
            app_id=config.app_id,
            private_key_pem=config.get_private_key_pem(),
        )
        self.reviewer = reviewer or GroqReviewer()

    async def process_task(self, task: ReviewTask) -> ReviewOutput:
        """Run the end-to-end autonomous review for an enqueued PR task."""
        logger.info(
            "Worker starting review task for %s/%s#%d (Delivery: %s)",
            task.owner,
            task.repo,
            task.pull_number,
            task.delivery_id,
        )

        # 1. Exchange GitHub App JWT for Installation Token
        token = self.auth.get_installation_token(task.installation_id)
        github_client = GitHubClient(token=token)

        # 2. Fetch Pull Request Diff
        diff_text = github_client.get_pull_request_diff(
            owner=task.owner,
            repo=task.repo,
            pull_number=task.pull_number,
        )

        if not diff_text.strip():
            logger.info(
                "Empty diff for %s/%s#%d, skipping review.",
                task.owner,
                task.repo,
                task.pull_number,
            )
            return ReviewOutput(
                summary="No changes found in pull request.",
                findings=[],
            )

        # 3. Instantiate Verifier and ProofEngine according to config
        verifier = FindingVerifier(reviewer=self.reviewer) if self.config.enable_verifier else None
        proof_engine = (
            ProofEngine(synthesizer=ReproductionSynthesizer(reviewer=self.reviewer))
            if self.config.enable_reproduction
            else None
        )

        # 4. Execute Autonomous Review
        runner = AgentRunner(
            reviewer=self.reviewer,
            verifier=verifier,
            proof_engine=proof_engine,
            enable_verifier=self.config.enable_verifier,
            enable_reproduction=self.config.enable_reproduction,
        )

        # Run synchronous review in threadpool to avoid blocking the event loop
        loop = asyncio.get_running_loop()
        review_output = await loop.run_in_executor(
            None,
            lambda: runner.review_to_output(
                diff_text=diff_text,
                repo_root=Path.cwd(),
                repo=f"{task.owner}/{task.repo}",
                pr_title=task.title,
                pr_description=task.body,
            ),
        )

        # 5. Publish Review to GitHub
        publisher = ReviewPublisher(github_client=github_client)
        publisher.publish_review(
            owner=task.owner,
            repo=task.repo,
            pull_number=task.pull_number,
            head_sha=task.head_sha,
            review_output=review_output,
            pr_title=task.title,
        )

        logger.info(
            "Worker completed review for %s/%s#%d: %d findings published.",
            task.owner,
            task.repo,
            task.pull_number,
            len(review_output.findings),
        )
        return review_output
