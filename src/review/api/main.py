"""Main FastAPI application factory and routing assembly."""

import logging
from typing import Any

from fastapi import FastAPI, Response, status
from fastapi.responses import HTMLResponse

from review.api.routes.feedback import router as feedback_router
from review.api.routes.repos import router as repos_router
from review.api.routes.reviews import router as reviews_router
from review.api.routes.webhooks import router as webhooks_router
from review.service.config import ServiceConfig
from review.telemetry.dashboard import render_dashboard_html
from review.telemetry.registry import get_global_registry

logger = logging.getLogger("review.api.main")


def create_app(config: ServiceConfig | None = None) -> FastAPI:
    """Create and configure the production FastAPI application."""
    app = FastAPI(
        title="Autonomous PR Review Agent API",
        version="0.2.0",
        description=(
            "Production GitHub App webhook ingestion, autonomous review engine, "
            "and telemetry service."
        ),
    )

    # 1. Mount Modular Subsystem Routers
    app.include_router(webhooks_router)
    app.include_router(reviews_router)
    app.include_router(feedback_router)
    app.include_router(repos_router)

    # 2. Health & Diagnostic Probes
    @app.get("/health", status_code=status.HTTP_200_OK, tags=["system"])
    async def health_check() -> dict[str, str]:
        """Health check endpoint for container orchestrators and load balancers."""
        return {
            "status": "healthy",
            "service": "review-agent",
            "version": "0.2.0",
        }

    # 3. Observability & Telemetry Endpoints
    @app.get("/metrics", status_code=status.HTTP_200_OK, tags=["observability"])
    async def prometheus_metrics() -> Response:
        """Prometheus OpenMetrics exposition endpoint."""
        registry = get_global_registry()
        return Response(
            content=registry.get_prometheus_metrics(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get("/api/v1/metrics", status_code=status.HTTP_200_OK, tags=["observability"])
    async def metrics_summary() -> dict[str, Any]:
        """Detailed JSON summary of review operational KPIs."""
        return get_global_registry().get_summary()

    @app.get("/dashboard", response_class=HTMLResponse, tags=["observability"])
    async def operational_dashboard() -> HTMLResponse:
        """Interactive dark-mode operational dashboard console."""
        registry = get_global_registry()
        html_content = render_dashboard_html(
            summary=registry.get_summary(),
            recent_traces=registry.get_recent_traces(limit=50),
        )
        return HTMLResponse(content=html_content)

    return app


# Default singleton application instance
app = create_app()

__all__ = ["app", "create_app"]
