"""Add pgvector embedding to repo precedents, signals feedback, and calibration metrics

Revision ID: 002_pgvector_hnsw_and_signals
Revises: 001_initial_schema
Create Date: 2026-10-07 18:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = "002_pgvector_hnsw_and_signals"
down_revision: str | None = "001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # 1. Add embedding column to repo_precedents
    if is_postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        op.add_column("repo_precedents", sa.Column("embedding", Vector(384), nullable=True))
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_repo_precedents_embedding "
            "ON repo_precedents USING hnsw (embedding vector_cosine_ops);"
        )
    else:
        op.add_column("repo_precedents", sa.Column("embedding", sa.JSON(), nullable=True))

    # 2. Add rule classification columns to findings
    op.add_column("findings", sa.Column("rule_id", sa.String(length=100), nullable=True))
    op.add_column("findings", sa.Column("rule_category", sa.String(length=100), nullable=True))
    op.create_index("ix_findings_rule_id", "findings", ["rule_id"])
    op.create_index("ix_findings_rule_category", "findings", ["rule_category"])

    # 3. Create review_feedback table
    op.create_table(
        "review_feedback",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("repository_id", sa.String(length=36), nullable=False),
        sa.Column("review_run_id", sa.String(length=36), nullable=True),
        sa.Column("finding_id", sa.String(length=36), nullable=True),
        sa.Column("pull_number", sa.Integer(), nullable=False),
        sa.Column("comment_id", sa.Integer(), nullable=True),
        sa.Column("reaction", sa.String(length=50), nullable=True),
        sa.Column("action", sa.String(length=50), nullable=False, server_default="reacted"),
        sa.Column("user_login", sa.String(length=100), nullable=True),
        sa.Column("rule_id", sa.String(length=100), nullable=True),
        sa.Column("rule_category", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["review_run_id"], ["review_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["finding_id"], ["findings.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_review_feedback_repository_id", "review_feedback", ["repository_id"])
    op.create_index("ix_review_feedback_review_run_id", "review_feedback", ["review_run_id"])
    op.create_index("ix_review_feedback_finding_id", "review_feedback", ["finding_id"])
    op.create_index("ix_review_feedback_pull_number", "review_feedback", ["pull_number"])
    op.create_index("ix_review_feedback_comment_id", "review_feedback", ["comment_id"])
    op.create_index("ix_review_feedback_rule_id", "review_feedback", ["rule_id"])
    op.create_index("ix_review_feedback_rule_category", "review_feedback", ["rule_category"])

    # 4. Create calibration_metrics table
    op.create_table(
        "calibration_metrics",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("repository_id", sa.String(length=36), nullable=False),
        sa.Column("target_type", sa.String(length=50), nullable=False),
        sa.Column("target_key", sa.String(length=100), nullable=False),
        sa.Column("acceptance_rate", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("total_signals", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("positive_signals", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("negative_signals", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("silenced", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "repository_id", "target_type", "target_key", name="uq_calib_repo_target"
        ),
    )
    op.create_index(
        "ix_calibration_metrics_repository_id", "calibration_metrics", ["repository_id"]
    )
    op.create_index("ix_calibration_metrics_target_key", "calibration_metrics", ["target_key"])


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    op.drop_table("calibration_metrics")
    op.drop_table("review_feedback")
    op.drop_index("ix_findings_rule_category", table_name="findings")
    op.drop_index("ix_findings_rule_id", table_name="findings")
    op.drop_column("findings", "rule_category")
    op.drop_column("findings", "rule_id")

    if is_postgres:
        op.execute("DROP INDEX IF EXISTS ix_repo_precedents_embedding;")

    op.drop_column("repo_precedents", "embedding")
