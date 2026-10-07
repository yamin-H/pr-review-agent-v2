"""Data models for repository memory, precedents, and semantic search."""

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
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PrecedentSearchResult(BaseModel):
    """A precedent matched from memory with its relevance score."""

    precedent: Precedent = Field(..., description="The matched repository precedent")
    score: float = Field(..., description="Relevance similarity score between 0.0 and 1.0")
