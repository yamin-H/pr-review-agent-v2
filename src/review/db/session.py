"""Asynchronous database session management and engine configuration."""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from review.db.models import Base

load_dotenv()

DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///.reviewer_database.db"


def get_database_url() -> str:
    """Retrieve async database URL from environment or fallback to local SQLite."""
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def create_engine(url: str | None = None, echo: bool = False) -> AsyncEngine:
    """Create an asynchronous SQLAlchemy engine configured for PostgreSQL or SQLite."""
    target_url = url or get_database_url()

    # Handle special SQLite in-memory configuration
    if ":memory:" in target_url:
        return create_async_engine(
            target_url,
            echo=echo,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

    # SQLite on-disk configuration
    if target_url.startswith("sqlite"):
        return create_async_engine(
            target_url,
            echo=echo,
            connect_args={"check_same_thread": False},
        )

    # PostgreSQL configuration with asyncpg
    return create_async_engine(
        target_url,
        echo=echo,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )


# Default engine and session maker instances
_engine: AsyncEngine | None = None
_session_maker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Get or create singleton global AsyncEngine."""
    global _engine
    if _engine is None:
        _engine = create_engine()
    return _engine


def get_session_maker(engine: AsyncEngine | None = None) -> async_sessionmaker[AsyncSession]:
    """Get or create singleton async sessionmaker."""
    global _session_maker
    active_engine = engine or get_engine()
    if _session_maker is None or engine is not None:
        _session_maker = async_sessionmaker(
            bind=active_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_maker


@asynccontextmanager
async def get_async_session(
    engine: AsyncEngine | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """Context manager providing an isolated transactional AsyncSession."""
    maker = get_session_maker(engine)
    async with maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db(engine: AsyncEngine | None = None) -> None:
    """Create all database tables defined in SQLAlchemy Base metadata."""
    active_engine = engine or get_engine()
    async with active_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_db(engine: AsyncEngine | None = None) -> None:
    """Drop all database tables (primarily used in test teardown)."""
    active_engine = engine or get_engine()
    async with active_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
