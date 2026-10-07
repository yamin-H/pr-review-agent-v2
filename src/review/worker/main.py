"""Main entry point and settings for the arq background queue worker."""

import logging
import os
from typing import Any

from arq.connections import RedisSettings

from review.db.session import get_engine, init_db
from review.worker.jobs import process_review_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("review.worker.main")

DEFAULT_REDIS_URL = "redis://localhost:6379/0"


def parse_redis_settings(url: str | None = None) -> RedisSettings:
    """Parse a Redis connection URL into an arq RedisSettings instance."""
    redis_url: str = url or os.getenv("REDIS_URL") or DEFAULT_REDIS_URL
    return RedisSettings.from_dsn(redis_url)


async def startup(ctx: dict[str, Any]) -> None:
    """Worker startup lifecycle hook: verify database connectivity and initialize tables."""
    logger.info("Starting review queue worker...")
    try:
        await init_db()
        logger.info("Worker database connection verified and schema initialized.")
    except Exception as e:
        logger.error("Failed to initialize database on worker startup: %s", e)
        raise


async def shutdown(ctx: dict[str, Any]) -> None:
    """Worker shutdown lifecycle hook: dispose database engine connections."""
    logger.info("Shutting down review queue worker...")
    engine = get_engine()
    await engine.dispose()
    logger.info("Worker connections closed successfully.")


class WorkerSettings:
    """Configuration class consumed by the arq worker runtime."""

    functions = [process_review_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = parse_redis_settings()
    max_jobs = int(os.getenv("WORKER_MAX_JOBS", "10"))
    job_timeout = int(os.getenv("WORKER_JOB_TIMEOUT", "300"))
    poll_delay = 0.5


if __name__ == "__main__":
    from arq import run_worker

    run_worker(WorkerSettings)  # type: ignore[arg-type]
