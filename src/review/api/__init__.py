"""API module providing FastAPI routes and application factory."""

from review.api.main import app, create_app

__all__ = ["app", "create_app"]
