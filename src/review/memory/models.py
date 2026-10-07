"""Data models for repository memory, precedents, semantic search, and feedback signals."""

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class Precedent(BaseModel):
    """A historical precedent, bug fix pattern, or team convention in a repository."""

    id: str = Field(..., description="Unique identifier for the precedent")
    repo: str = Field(..., description="Target repository name (owner/repo)")
    title: str = Field(..., description="Short summary of the rule, convention, or past bug")
    description: str = Field(
        ..., description="Detailed explanation of the issue and required resolution"
    )
    citation: str = Field(
        ...,
        description="Origin reference (e.g. 'PR #412', 'CVE-2018-18074', 'commit a1b2c3d')",
    )
    file_pattern: str = Field(
        default="*", description="Glob pattern of relevant files (e.g. 'src/auth/*.py')"
    )
    code_snippet: str | None = Field(
        default=None, description="Illustrative buggy or fixed code snippet"
    )
    embedding: list[float] | None = Field(
        default=None, description="384-dimensional dense semantic embedding vector"
    )
    category: str = Field(
        default="general", description="Precedent category (e.g. security, bug, convention)"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PrecedentSearchResult(BaseModel):
    """A precedent matched from memory with its relevance score."""

    precedent: Precedent = Field(..., description="The matched repository precedent")
    score: float = Field(..., description="Relevance similarity score between 0.0 and 1.0")


class SignalFeedback(BaseModel):
    """Normalized developer feedback signal received from review interactions."""

    repository_id: str = Field(..., description="Target repository UUID or name")
    review_run_id: str | None = Field(default=None, description="Associated review run ID")
    finding_id: str | None = Field(default=None, description="Specific finding UUID")
    pull_number: int = Field(..., description="Target pull request number")
    comment_id: int | None = Field(default=None, description="GitHub review comment ID")
    reaction: str | None = Field(
        default=None, description="Emoji reaction string, e.g. +1, -1, confused, heart"
    )
    action: str = Field(
        default="reacted", description="Signal action: reacted, resolved, dismissed, outdated"
    )
    user_login: str | None = Field(default=None, description="GitHub username submitting signal")
    rule_id: str | None = Field(default=None, description="Rule identifier if associated")
    rule_category: str | None = Field(default=None, description="Rule category if associated")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CalibrationMetric(BaseModel):
    """Aggregated acceptance rate and silencing decision for a repo, category, or rule."""

    target_type: str = Field(..., description="Scope type: repo, category, or rule")
    target_key: str = Field(..., description="Target identifier (repo ID, category, or rule name)")
    acceptance_rate: float = Field(default=1.0, ge=0.0, le=1.0, description="Acceptance ratio")
    total_signals: int = Field(default=0, ge=0, description="Total developer signals observed")
    positive_signals: int = Field(default=0, ge=0, description="Positive signal count")
    negative_signals: int = Field(default=0, ge=0, description="Negative signal count")
    silenced: bool = Field(default=False, description="Whether rule/category should be silenced")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
