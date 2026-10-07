"""Reviews REST API routes for querying review history, findings, and execution traces."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from review.api.deps import get_db_session
from review.db.models import Repository, ReviewRun
from review.telemetry.registry import get_global_registry

router = APIRouter(prefix="/api/v1", tags=["reviews"])


@router.get("/reviews")
async def list_reviews(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    repo: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """List review runs with optional repository and status filters."""
    query = (
        select(ReviewRun)
        .options(selectinload(ReviewRun.repository))
        .order_by(desc(ReviewRun.started_at))
        .offset(offset)
        .limit(limit)
    )

    if repo:
        query = query.join(ReviewRun.repository).where(Repository.full_name == repo.lower().strip())
    if status_filter:
        query = query.where(ReviewRun.status == status_filter)

    result = await session.execute(query)
    runs = result.scalars().all()

    return [
        {
            "id": r.id,
            "repository": r.repository.full_name if r.repository else "",
            "pull_number": r.pull_number,
            "head_sha": r.head_sha,
            "status": r.status,
            "summary": r.summary,
            "duration_ms": r.duration_ms,
            "total_tokens": r.total_tokens,
            "cost_usd": r.cost_usd,
            "started_at": r.started_at.isoformat(),
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }
        for r in runs
    ]


@router.get("/reviews/{review_id}")
async def get_review_detail(
    review_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Retrieve full details for a review run including inline findings and execution trace."""
    query = (
        select(ReviewRun)
        .options(
            selectinload(ReviewRun.repository),
            selectinload(ReviewRun.findings),
            selectinload(ReviewRun.trace),
        )
        .where(ReviewRun.id == review_id)
    )
    result = await session.execute(query)
    run = result.scalar_one_or_none()

    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Review run '{review_id}' not found.",
        )

    return {
        "id": run.id,
        "repository": run.repository.full_name if run.repository else "",
        "pull_number": run.pull_number,
        "head_sha": run.head_sha,
        "status": run.status,
        "summary": run.summary,
        "duration_ms": run.duration_ms,
        "total_tokens": run.total_tokens,
        "cost_usd": run.cost_usd,
        "started_at": run.started_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "findings": [
            {
                "id": f.id,
                "file": f.file_path,
                "line": f.line_number,
                "title": f.title,
                "body": f.body,
                "severity": f.severity,
                "status": f.status,
                "is_reproduced": f.is_reproduced,
                "reproduction_output": f.reproduction_output,
                "citation": f.precedent_citation,
            }
            for f in run.findings
        ],
        "trace": {
            "trace_id": run.trace.trace_id,
            "prompt_tokens": run.trace.prompt_tokens,
            "completion_tokens": run.trace.completion_tokens,
            "spans": run.trace.spans,
            "latency_breakdown": run.trace.latency_breakdown,
        }
        if run.trace
        else None,
    }


@router.get("/traces")
async def get_recent_traces(limit: int = 20) -> list[dict[str, Any]]:
    """Retrieve in-memory review traces from global registry (backward-compatible endpoint)."""
    return get_global_registry().get_recent_traces(limit=limit)
