"""SQLAlchemy 2.0 declarative database models with tenant isolation."""

import uuid
from datetime import UTC, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)
from sqlalchemy.types import TypeDecorator, TypeEngine


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy database models."""

    pass


class VectorType(TypeDecorator[list[float]]):
    """Platform-independent Vector type rendering Vector(384) on PostgreSQL and JSON elsewhere."""

    impl = Vector(384)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Vector(384))
        return dialect.type_descriptor(JSON())


class Tenant(Base):
    """Organization or tenant account holding repositories and reviews."""

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    github_org_id: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan_tier: Mapped[str] = mapped_column(String(50), default="standard", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    repositories: Mapped[list["Repository"]] = relationship(
        "Repository", back_populates="tenant", cascade="all, delete-orphan"
    )


class Repository(Base):
    """GitHub repository associated with a specific tenant."""

    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    github_repo_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="repositories")
    reviews: Mapped[list["ReviewRun"]] = relationship(
        "ReviewRun", back_populates="repository", cascade="all, delete-orphan"
    )
    precedents: Mapped[list["RepoPrecedent"]] = relationship(
        "RepoPrecedent", back_populates="repository", cascade="all, delete-orphan"
    )
    feedback_records: Mapped[list["ReviewFeedbackRecord"]] = relationship(
        "ReviewFeedbackRecord", back_populates="repository", cascade="all, delete-orphan"
    )
    calibration_metrics: Mapped[list["CalibrationMetricRecord"]] = relationship(
        "CalibrationMetricRecord", back_populates="repository", cascade="all, delete-orphan"
    )


class ReviewRun(Base):
    """An execution run of a pull request code review."""

    __tablename__ = "review_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    repository_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    pull_number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    base_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False, index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    repository: Mapped["Repository"] = relationship("Repository", back_populates="reviews")
    findings: Mapped[list["FindingRecord"]] = relationship(
        "FindingRecord", back_populates="review_run", cascade="all, delete-orphan"
    )
    trace: Mapped["ExecutionTrace | None"] = relationship(
        "ExecutionTrace", back_populates="review_run", uselist=False, cascade="all, delete-orphan"
    )
    feedback_records: Mapped[list["ReviewFeedbackRecord"]] = relationship(
        "ReviewFeedbackRecord", back_populates="review_run"
    )


class FindingRecord(Base):
    """An inline review finding produced during a review run."""

    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    review_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("review_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="verified", nullable=False)
    is_reproduced: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    reproduction_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    reproduction_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    precedent_citation: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rule_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    rule_category: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    review_run: Mapped["ReviewRun"] = relationship("ReviewRun", back_populates="findings")
    feedback_records: Mapped[list["ReviewFeedbackRecord"]] = relationship(
        "ReviewFeedbackRecord", back_populates="finding"
    )


class ExecutionTrace(Base):
    """Detailed operational trace record for latency and span breakdown."""

    __tablename__ = "execution_traces"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    review_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("review_runs.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    trace_id: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    spans: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    latency_breakdown: Mapped[dict[str, float]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    review_run: Mapped["ReviewRun"] = relationship("ReviewRun", back_populates="trace")


class RepoPrecedent(Base):
    """Historical bug pattern, CVE fix, or team convention stored for a repository."""

    __tablename__ = "repo_precedents"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    repository_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    citation: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    file_pattern: Mapped[str] = mapped_column(String(255), default="*", nullable=False)
    code_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(VectorType(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    repository: Mapped["Repository"] = relationship("Repository", back_populates="precedents")


class ReviewFeedbackRecord(Base):
    """Developer feedback signal (reaction, resolution, dismissal) on review comments."""

    __tablename__ = "review_feedback"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    repository_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    review_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("review_runs.id", ondelete="SET NULL"), index=True, nullable=True
    )
    finding_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("findings.id", ondelete="SET NULL"), index=True, nullable=True
    )
    pull_number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    comment_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    reaction: Mapped[str | None] = mapped_column(String(50), nullable=True)
    action: Mapped[str] = mapped_column(String(50), default="reacted", nullable=False)
    user_login: Mapped[str | None] = mapped_column(String(100), nullable=True)
    rule_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    rule_category: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    repository: Mapped["Repository"] = relationship(
        "Repository", back_populates="feedback_records"
    )
    review_run: Mapped["ReviewRun | None"] = relationship(
        "ReviewRun", back_populates="feedback_records"
    )
    finding: Mapped["FindingRecord | None"] = relationship(
        "FindingRecord", back_populates="feedback_records"
    )


class CalibrationMetricRecord(Base):
    """Aggregate signal tracking and silence thresholds per repository, category, or rule."""

    __tablename__ = "calibration_metrics"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    repository_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    acceptance_rate: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    total_signals: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    positive_signals: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    negative_signals: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    silenced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    repository: Mapped["Repository"] = relationship(
        "Repository", back_populates="calibration_metrics"
    )
