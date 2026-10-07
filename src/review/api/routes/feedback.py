"""API routes for developer feedback signals, calibration metrics, and repository memory."""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from review.api.deps import get_db_session
from review.db.models import CalibrationMetricRecord, Repository
from review.memory.models import Precedent, SignalFeedback
from review.memory.signals import SignalTracker
from review.memory.store import PgVectorMemoryStore

logger = logging.getLogger("review.api.routes.feedback")
router = APIRouter(prefix="/api/v1/repos", tags=["memory-and-signals"])


class FeedbackSubmission(BaseModel):
    """Developer feedback payload for a review comment or finding."""

    pull_number: int = Field(..., description="Target pull request number")
    finding_id: str | None = Field(default=None, description="Optional finding UUID")
    review_run_id: str | None = Field(default=None, description="Optional review run UUID")
    comment_id: int | None = Field(default=None, description="GitHub comment ID")
    reaction: str | None = Field(
        default=None, description="Reaction type: +1, -1, confused, heart, etc."
    )
    action: str = Field(
        default="reacted", description="Action: reacted, resolved, dismissed, outdated"
    )
    user_login: str | None = Field(default=None, description="GitHub username")
    rule_id: str | None = Field(default=None, description="Rule ID")
    rule_category: str | None = Field(default=None, description="Rule category")


class PrecedentSubmission(BaseModel):
    """Precedent creation payload for repository memory."""

    title: str = Field(..., description="Title of convention or bug fix")
    description: str = Field(..., description="Detailed description")
    citation: str = Field(..., description="Citation reference, e.g. PR #123, CVE-2024-xxxx")
    file_pattern: str = Field(default="*", description="Glob file pattern")
    code_snippet: str | None = Field(default=None, description="Code snippet")
    category: str = Field(default="general", description="Category: bug, security, convention")


@router.post("/{repo_id}/feedback", status_code=status.HTTP_201_CREATED)
async def submit_feedback(
    repo_id: str,
    feedback_in: FeedbackSubmission,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Submit developer feedback (reaction, resolution, dismissal) on review findings."""
    tracker = SignalTracker(session=session)
    feedback = SignalFeedback(
        repository_id=repo_id,
        pull_number=feedback_in.pull_number,
        finding_id=feedback_in.finding_id,
        review_run_id=feedback_in.review_run_id,
        comment_id=feedback_in.comment_id,
        reaction=feedback_in.reaction,
        action=feedback_in.action,
        user_login=feedback_in.user_login,
        rule_id=feedback_in.rule_id,
        rule_category=feedback_in.rule_category,
    )

    try:
        feedback_rec, updated_metrics = await tracker.record_feedback(feedback)
        await session.commit()
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except Exception as e:
        logger.error("Failed to record feedback signal: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to record feedback signal",
        ) from e

    return {
        "status": "recorded",
        "feedback_id": feedback_rec.id,
        "metrics_updated": [
            {
                "target_type": m.target_type,
                "target_key": m.target_key,
                "acceptance_rate": m.acceptance_rate,
                "total_signals": m.total_signals,
                "silenced": m.silenced,
            }
            for m in updated_metrics
        ],
    }


@router.get("/{repo_id}/calibration", status_code=status.HTTP_200_OK)
async def get_repo_calibration(
    repo_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[dict[str, Any]]:
    """Retrieve all calibration metrics and silence thresholds for a repository."""
    tracker = SignalTracker(session=session)
    try:
        resolved_repo_id = await tracker._resolve_repo_id(repo_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    stmt = select(CalibrationMetricRecord).where(
        CalibrationMetricRecord.repository_id == resolved_repo_id
    )
    res = await session.execute(stmt)
    metrics = res.scalars().all()

    return [
        {
            "id": m.id,
            "target_type": m.target_type,
            "target_key": m.target_key,
            "acceptance_rate": m.acceptance_rate,
            "total_signals": m.total_signals,
            "positive_signals": m.positive_signals,
            "negative_signals": m.negative_signals,
            "silenced": m.silenced,
            "updated_at": m.updated_at.isoformat(),
        }
        for m in metrics
    ]


@router.post("/{repo_id}/precedents", status_code=status.HTTP_201_CREATED)
async def create_repo_precedent(
    repo_id: str,
    precedent_in: PrecedentSubmission,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Ingest a new precedent with semantic vector embedding and automatic deduplication."""
    store = PgVectorMemoryStore(session=session)
    try:
        resolved_repo_id = await store._resolve_repo_id(repo_id)
        repo_stmt = select(Repository).where(Repository.id == resolved_repo_id)
        repo_res = await session.execute(repo_stmt)
        repo_obj = repo_res.scalar_one()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Repository not found: {repo_id}"
        ) from e

    precedent = Precedent(
        id=f"prec_{abs(hash(precedent_in.title + precedent_in.citation)) % 100000000}",
        repo=repo_obj.full_name,
        title=precedent_in.title,
        description=precedent_in.description,
        citation=precedent_in.citation,
        file_pattern=precedent_in.file_pattern,
        code_snippet=precedent_in.code_snippet,
        category=precedent_in.category,
    )

    saved_prec, is_new = await store.deduplicate_and_add(precedent, repo_id=resolved_repo_id)
    await session.commit()

    return {
        "status": "created" if is_new else "merged",
        "precedent": saved_prec.model_dump(exclude={"embedding"}),
        "is_new": is_new,
    }


@router.get("/{repo_id}/precedents", status_code=status.HTTP_200_OK)
async def search_repo_precedents(
    repo_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    query: str = Query(default="", description="Search query text"),
    file_path: str = Query(default="", description="Target file path pattern filter"),
    min_score: float = Query(default=0.1, ge=0.0, le=1.0, description="Minimum similarity score"),
    limit: int = Query(default=5, ge=1, le=50, description="Maximum results"),
) -> list[dict[str, Any]]:
    """Search repository precedents using native pgvector cosine distance or list candidates."""
    store = PgVectorMemoryStore(session=session)
    try:
        resolved_repo_id = await store._resolve_repo_id(repo_id)
        repo_stmt = select(Repository).where(Repository.id == resolved_repo_id)
        repo_res = await session.execute(repo_stmt)
        repo_obj = repo_res.scalar_one()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Repository not found: {repo_id}"
        ) from e

    if query:
        matches = await store.search_precedents(
            query=query,
            repo=repo_obj.full_name,
            file_path=file_path,
            min_score=min_score,
            limit=limit,
        )
        return [
            {
                "score": m.score,
                "precedent": m.precedent.model_dump(exclude={"embedding"}),
            }
            for m in matches
        ]

    precedents = await store.list_precedents(repo=repo_obj.full_name)
    return [
        {
            "score": 1.0,
            "precedent": p.model_dump(exclude={"embedding"}),
        }
        for p in precedents[:limit]
    ]
