"""Initial database schema with pgvector extension and tenant isolation

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-10-07 16:40:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0. Enable pgvector extension if on PostgreSQL
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # 1. Tenants table
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("github_org_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("plan_tier", sa.String(length=50), nullable=False, server_default="standard"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tenants_github_org_id", "tenants", ["github_org_id"], unique=True)

    # 2. Repositories table
    op.create_table(
        "repositories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("github_repo_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_repositories_full_name", "repositories", ["full_name"], unique=True)
    op.create_index("ix_repositories_tenant_id", "repositories", ["tenant_id"])

    # 3. Review Runs table
    op.create_table(
        "review_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("repository_id", sa.String(length=36), nullable=False),
        sa.Column("pull_number", sa.Integer(), nullable=False),
        sa.Column("head_sha", sa.String(length=40), nullable=False),
        sa.Column("base_sha", sa.String(length=40), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_review_runs_idempotency_key", "review_runs", ["idempotency_key"], unique=True
    )
    op.create_index("ix_review_runs_repository_id", "review_runs", ["repository_id"])
    op.create_index("ix_review_runs_pull_number", "review_runs", ["pull_number"])
    op.create_index("ix_review_runs_head_sha", "review_runs", ["head_sha"])
    op.create_index("ix_review_runs_status", "review_runs", ["status"])

    # 4. Findings table
    op.create_table(
        "findings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("review_run_id", sa.String(length=36), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="verified"),
        sa.Column("is_reproduced", sa.Boolean(), nullable=True),
        sa.Column("reproduction_code", sa.Text(), nullable=True),
        sa.Column("reproduction_output", sa.Text(), nullable=True),
        sa.Column("precedent_citation", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["review_run_id"], ["review_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_findings_review_run_id", "findings", ["review_run_id"])

    # 5. Execution Traces table
    op.create_table(
        "execution_traces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("review_run_id", sa.String(length=36), nullable=False),
        sa.Column("trace_id", sa.String(length=100), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("spans", sa.JSON(), nullable=False),
        sa.Column("latency_breakdown", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["review_run_id"], ["review_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_execution_traces_review_run_id", "execution_traces", ["review_run_id"], unique=True
    )
    op.create_index("ix_execution_traces_trace_id", "execution_traces", ["trace_id"], unique=True)

    # 6. Repo Precedents table
    op.create_table(
        "repo_precedents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("repository_id", sa.String(length=36), nullable=False),
        sa.Column("citation", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("file_pattern", sa.String(length=255), nullable=False, server_default="*"),
        sa.Column("code_snippet", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_repo_precedents_repository_id", "repo_precedents", ["repository_id"])


def downgrade() -> None:
    op.drop_table("repo_precedents")
    op.drop_table("execution_traces")
    op.drop_table("findings")
    op.drop_table("review_runs")
    op.drop_table("repositories")
    op.drop_table("tenants")
