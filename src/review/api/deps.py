"""FastAPI dependency injection providers for database, redis, and configuration."""

from collections.abc import AsyncGenerator

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from sqlalchemy.ext.asyncio import AsyncSession

from review.db.session import get_async_session
from review.github.webhooks import DeliveryDeduplicator
from review.service.config import ServiceConfig

_deduplicator = DeliveryDeduplicator()
_redis_pool: ArqRedis | None = None


def get_service_config() -> ServiceConfig:
    """Provide service configuration singleton."""
    return ServiceConfig()


def get_delivery_deduplicator() -> DeliveryDeduplicator:
    """Provide global delivery deduplicator singleton."""
    return _deduplicator


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency yielding an asynchronous database session."""
    async with get_async_session() as session:
        yield session


async def get_redis_queue() -> ArqRedis | None:
    """Dependency yielding the shared arq Redis connection pool."""
    global _redis_pool
    if _redis_pool is None:
        try:
            config = get_service_config()
            redis_url = getattr(config, "redis_url", "redis://localhost:6379/0")
            _redis_pool = await create_pool(RedisSettings.from_dsn(redis_url))
        except Exception:
            _redis_pool = None
    return _redis_pool
