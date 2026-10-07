"""Repository configuration and management REST API routes."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from review.api.deps import get_db_session
from review.db.models import Repository

router = APIRouter(prefix="/api/v1/repos", tags=["repositories"])


class RepoSettingsUpdate(BaseModel):
    """Schema for updating repository-level review configurations."""

    is_active: bool | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


@router.get("")
async def list_repositories(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    limit: int = Query(50, ge=1, le=100),
) -> list[dict[str, Any]]:
    """List all registered repositories."""
    result = await session.execute(
        select(Repository).options(selectinload(Repository.tenant)).limit(limit)
    )
    repos = result.scalars().all()

    return [
        {
            "id": r.id,
            "full_name": r.full_name,
            "tenant_name": r.tenant.name if r.tenant else "",
            "is_active": r.is_active,
            "settings": r.settings,
            "created_at": r.created_at.isoformat(),
        }
        for r in repos
    ]


@router.get("/{owner}/{repo_name}")
async def get_repository(
    owner: str,
    repo_name: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Retrieve metadata and settings for a specific repository."""
    full_name = f"{owner.lower()}/{repo_name.lower()}"
    result = await session.execute(
        select(Repository)
        .options(selectinload(Repository.tenant))
        .where(Repository.full_name == full_name)
    )
    repo = result.scalar_one_or_none()

    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{full_name}' not found.",
        )

    return {
        "id": repo.id,
        "full_name": repo.full_name,
        "tenant_id": repo.tenant_id,
        "tenant_name": repo.tenant.name if repo.tenant else "",
        "is_active": repo.is_active,
        "settings": repo.settings,
        "created_at": repo.created_at.isoformat(),
    }


@router.put("/{owner}/{repo_name}/settings")
async def update_repository_settings(
    owner: str,
    repo_name: str,
    update: RepoSettingsUpdate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Update settings and active status for a repository."""
    full_name = f"{owner.lower()}/{repo_name.lower()}"
    result = await session.execute(
        select(Repository).where(Repository.full_name == full_name)
    )
    repo = result.scalar_one_or_none()

    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{full_name}' not found.",
        )

    if update.is_active is not None:
        repo.is_active = update.is_active
    if update.settings:
        merged_settings = dict(repo.settings)
        merged_settings.update(update.settings)
        repo.settings = merged_settings

    await session.commit()

    return {
        "status": "updated",
        "full_name": repo.full_name,
        "is_active": repo.is_active,
        "settings": repo.settings,
    }
