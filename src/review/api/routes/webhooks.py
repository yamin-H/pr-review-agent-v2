"""GitHub Webhook ingestion API route."""

import json
import logging
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse

from review.api.auth import verify_webhook_hmac
from review.api.deps import get_delivery_deduplicator, get_redis_queue
from review.db.session import get_async_session
from review.github.webhooks import DeliveryDeduplicator, PullRequestWebhookPayload
from review.memory.models import SignalFeedback
from review.memory.signals import SignalTracker
from review.worker.jobs import process_review_job

logger = logging.getLogger("review.api.routes.webhooks")
router = APIRouter(prefix="/api/v1/github", tags=["webhooks"])


@router.post("/webhooks", status_code=status.HTTP_202_ACCEPTED)
async def handle_github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    payload_bytes: Annotated[bytes, Depends(verify_webhook_hmac)],
    deduplicator: Annotated[DeliveryDeduplicator, Depends(get_delivery_deduplicator)],
    redis_queue: Annotated[Any, Depends(get_redis_queue)] = None,
    x_github_event: Annotated[str | None, Header(alias="X-GitHub-Event")] = None,
    x_github_delivery: Annotated[str | None, Header(alias="X-GitHub-Delivery")] = None,
) -> Response:
    """Ingest GitHub webhook events with HMAC signature verification and replay protection."""
    delivery_id = x_github_delivery or ""

    # 1. Replay defense check
    if delivery_id and deduplicator.is_duplicate(delivery_id):
        logger.info("Ignoring duplicate delivery UUID: %s", delivery_id)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "ignored",
                "reason": "duplicate_delivery",
                "delivery_id": delivery_id,
            },
        )

    # 2. Ping handshake
    if x_github_event == "ping":
        logger.info("Received GitHub ping event (Delivery: %s)", delivery_id)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "pong", "delivery_id": delivery_id},
        )

    # 3. Pull Request events
    if x_github_event == "pull_request":
        try:
            raw_json: dict[str, Any] = json.loads(payload_bytes.decode("utf-8"))
        except Exception as e:
            logger.error("Failed to parse JSON body: %s", e)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON payload.",
            ) from e

        pr_info = PullRequestWebhookPayload.from_raw_payload(raw_json, delivery_id=delivery_id)

        # Trigger only on reviewable changes
        if pr_info.action not in {"opened", "synchronize", "reopened"}:
            logger.debug(
                "Ignoring PR action '%s' for %s/%s#%d",
                pr_info.action,
                pr_info.owner,
                pr_info.repo,
                pr_info.pull_number,
            )
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"status": "ignored", "action": pr_info.action},
            )

        task_payload = pr_info.model_dump()

        # Enqueue via Redis arq queue if available, otherwise fallback to FastAPI background tasks
        if redis_queue is not None:
            try:
                await redis_queue.enqueue_job("process_review_job", payload=task_payload)
                logger.info(
                    "Enqueued review job to arq Redis for %s/%s#%d",
                    pr_info.owner,
                    pr_info.repo,
                    pr_info.pull_number,
                )
            except Exception as e:
                logger.warning(
                    "Redis enqueue failed (%s), falling back to in-process execution.",
                    e,
                )
                background_tasks.add_task(process_review_job, None, task_payload)
        else:
            background_tasks.add_task(process_review_job, None, task_payload)

        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "status": "accepted",
                "delivery_id": delivery_id,
                "pull_number": pr_info.pull_number,
                "owner": pr_info.owner,
                "repo": pr_info.repo,
                "head_sha": pr_info.head_sha,
            },
        )

    # 4. Review Comment & Reaction Signals
    if x_github_event in ("pull_request_review_comment", "reaction"):
        try:
            raw_json = json.loads(payload_bytes.decode("utf-8"))
        except Exception as e:
            logger.error("Failed to parse JSON body: %s", e)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON payload.",
            ) from e

        action = raw_json.get("action", "reacted")
        repo_data = raw_json.get("repository", {})
        repo_name = repo_data.get("full_name") or str(repo_data.get("id", ""))
        pull_number = raw_json.get("pull_request", {}).get("number") or 0
        comment_data = raw_json.get("comment", {})
        comment_id = comment_data.get("id")
        user_login = raw_json.get("sender", {}).get("login")
        reaction = raw_json.get("reaction", {}).get("content")

        if repo_name:
            feedback = SignalFeedback(
                repository_id=repo_name,
                pull_number=pull_number,
                comment_id=comment_id,
                reaction=reaction,
                action=action,
                user_login=user_login,
            )
            try:
                async with get_async_session() as session:
                    tracker = SignalTracker(session=session)
                    await tracker.record_feedback(feedback)
                    await session.commit()
                logger.info(
                    "Recorded webhook signal: repo=%s, pr=#%d, action=%s, reaction=%s",
                    repo_name,
                    pull_number,
                    action,
                    reaction,
                )
            except Exception as ex:
                logger.warning("Failed to record webhook signal feedback: %s", ex)

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "recorded", "event": x_github_event, "action": action},
        )

    # 5. Other events ignored
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "ignored", "event": x_github_event},
    )
