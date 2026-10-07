"""FastAPI REST API layer and GitHub webhook ingestion service."""

import json
import logging
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse

from review.service.config import ServiceConfig
from review.service.security import DeliveryDeduplicator, verify_github_signature
from review.service.worker import BackgroundReviewWorker, ReviewTask
from review.telemetry.dashboard import render_dashboard_html
from review.telemetry.registry import get_global_registry

logger = logging.getLogger("review.service.api")


def create_app(
    config: ServiceConfig | None = None,
    worker: BackgroundReviewWorker | None = None,
    deduplicator: DeliveryDeduplicator | None = None,
) -> FastAPI:
    """Create and configure the FastAPI webhook application."""
    app_config = config or ServiceConfig()
    app_worker = worker or BackgroundReviewWorker(config=app_config)
    app_deduplicator = deduplicator or DeliveryDeduplicator()

    app = FastAPI(
        title="Autonomous PR Review Agent API",
        version="0.1.0",
        description="GitHub App webhook ingestion and autonomous pull request review service.",
    )

    @app.get("/health", status_code=status.HTTP_200_OK)
    async def health_check() -> dict[str, str]:
        """Health check endpoint for container probes and load balancers."""
        return {
            "status": "healthy",
            "service": "review-agent",
            "version": "0.1.0",
        }

    @app.post("/api/v1/github/webhooks")
    async def handle_github_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
        x_github_event: str | None = Header(None, alias="X-GitHub-Event"),
        x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
        x_github_delivery: str | None = Header(None, alias="X-GitHub-Delivery"),
    ) -> Response:
        """Handle incoming GitHub webhook events with HMAC security and replay defense."""
        raw_body = await request.body()

        # 1. HMAC-SHA256 Signature Verification
        if app_config.webhook_secret and not verify_github_signature(
            payload_bytes=raw_body,
            signature_header=x_hub_signature_256,
            secret=app_config.webhook_secret,
        ):
            logger.warning("Unauthorized webhook: invalid HMAC signature.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid HMAC signature.",
            )

        # 2. Replay Defense via Delivery ID Deduplication
        delivery_id = x_github_delivery or ""
        if delivery_id and app_deduplicator.is_duplicate(delivery_id):
            logger.info("Ignoring duplicate delivery ID: %s", delivery_id)
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "status": "ignored",
                    "reason": "duplicate_delivery",
                    "delivery_id": delivery_id,
                },
            )

        # 3. Handle GitHub Ping / Handshake
        if x_github_event == "ping":
            logger.info("Received GitHub ping handshake (Delivery: %s)", delivery_id)
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"status": "pong", "delivery_id": delivery_id},
            )

        # 4. Handle Pull Request Lifecycle Events
        if x_github_event == "pull_request":
            try:
                payload: dict[str, Any] = json.loads(raw_body.decode("utf-8"))
            except Exception as e:
                logger.error("Failed to parse webhook JSON payload: %s", e)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid JSON payload.",
                ) from e

            action = payload.get("action", "")
            # Only trigger on code changes / PR opens
            if action not in {"opened", "synchronize", "reopened"}:
                logger.debug("Ignoring pull_request action: %s", action)
                return JSONResponse(
                    status_code=status.HTTP_200_OK,
                    content={"status": "ignored", "action": action},
                )

            pr_data = payload.get("pull_request", {})
            repo_data = payload.get("repository", {})
            installation_data = payload.get("installation", {})

            task = ReviewTask(
                delivery_id=delivery_id or "adhoc",
                installation_id=int(installation_data.get("id", 0)),
                owner=repo_data.get("owner", {}).get("login", ""),
                repo=repo_data.get("name", ""),
                pull_number=int(pr_data.get("number", 0)),
                head_sha=pr_data.get("head", {}).get("sha", ""),
                title=pr_data.get("title", ""),
                body=pr_data.get("body", "") or "",
            )

            # Enqueue review in background to return 202 Accepted immediately
            background_tasks.add_task(app_worker.process_task, task)

            logger.info(
                "Enqueued background review for %s/%s#%d (Delivery: %s)",
                task.owner,
                task.repo,
                task.pull_number,
                task.delivery_id,
            )
            return JSONResponse(
                status_code=status.HTTP_202_ACCEPTED,
                content={
                    "status": "accepted",
                    "delivery_id": delivery_id,
                    "pull_number": task.pull_number,
                    "owner": task.owner,
                    "repo": task.repo,
                },
            )

        # Other events are ignored
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ignored", "event": x_github_event},
        )

    @app.get("/metrics", status_code=status.HTTP_200_OK)
    async def prometheus_metrics() -> Response:
        """Prometheus metrics exposition endpoint."""
        registry = get_global_registry()
        return Response(
            content=registry.get_prometheus_metrics(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get("/api/v1/metrics", status_code=status.HTTP_200_OK)
    async def metrics_summary() -> dict[str, Any]:
        """Detailed operational metrics summary endpoint."""
        return get_global_registry().get_summary()

    @app.get("/api/v1/traces", status_code=status.HTTP_200_OK)
    async def recent_traces(limit: int = 20) -> list[dict[str, Any]]:
        """Recent review traces endpoint."""
        return get_global_registry().get_recent_traces(limit=limit)

    @app.get("/dashboard", response_class=HTMLResponse)
    async def operational_dashboard() -> HTMLResponse:
        """Interactive dark-mode operational dashboard."""
        registry = get_global_registry()
        html_content = render_dashboard_html(
            summary=registry.get_summary(),
            recent_traces=registry.get_recent_traces(limit=50),
        )
        return HTMLResponse(content=html_content)

    return app


# Default app instance
app = create_app()
