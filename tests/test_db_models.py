from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from review.db.models import (
    ExecutionTrace,
    FindingRecord,
    RepoPrecedent,
    Repository,
    ReviewRun,
    Tenant,
)
from review.db.session import create_engine, drop_db, get_session_maker, init_db


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    """Create in-memory SQLite database and yield session."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    maker = get_session_maker(engine)

    async with maker() as s:
        yield s

    await drop_db(engine)
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_and_retrieve_tenant(session: AsyncSession) -> None:
    tenant = Tenant(
        github_org_id="github-org-123",
        name="Test Organization",
        plan_tier="pro",
    )
    session.add(tenant)
    await session.commit()

    res = await session.execute(select(Tenant).where(Tenant.github_org_id == "github-org-123"))
    loaded = res.scalar_one()

    assert loaded.id is not None
    assert loaded.name == "Test Organization"
    assert loaded.plan_tier == "pro"
    assert loaded.created_at is not None


@pytest.mark.asyncio
async def test_duplicate_org_id_raises_integrity_error(session: AsyncSession) -> None:
    t1 = Tenant(github_org_id="duplicate-org", name="Org 1")
    t2 = Tenant(github_org_id="duplicate-org", name="Org 2")

    session.add(t1)
    await session.commit()

    session.add(t2)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_review_run_with_findings_and_trace(session: AsyncSession) -> None:
    tenant = Tenant(github_org_id="org-trace", name="Trace Org")
    session.add(tenant)
    await session.flush()

    repo = Repository(tenant_id=tenant.id, full_name="org-trace/repo", github_repo_id=999)
    session.add(repo)
    await session.flush()

    run = ReviewRun(
        repository_id=repo.id,
        pull_number=10,
        head_sha="1234567890abcdef1234567890abcdef12345678",
        idempotency_key="review:org-trace/repo:10:1234567890abcdef1234567890abcdef12345678",
        status="success",
        duration_ms=1250.0,
        total_tokens=4500,
        cost_usd=0.0025,
    )
    session.add(run)
    await session.flush()

    finding = FindingRecord(
        review_run_id=run.id,
        file_path="src/main.py",
        line_number=20,
        title="SQL injection risk",
        body="Use parameterized query",
        severity="high",
        status="verified",
        is_reproduced=True,
        reproduction_code="def test(): assert False",
        reproduction_output="AssertionError",
        precedent_citation="CVE-2024-0001",
    )
    session.add(finding)

    trace = ExecutionTrace(
        review_run_id=run.id,
        trace_id="trace_test_123",
        prompt_tokens=3000,
        completion_tokens=1500,
        spans=[{"name": "llm_step_1", "duration_ms": 800.0}],
        latency_breakdown={"llm_reasoning": 800.0},
    )
    session.add(trace)
    await session.commit()

    # Query with eager loading
    res = await session.execute(
        select(ReviewRun)
        .options(
            selectinload(ReviewRun.findings),
            selectinload(ReviewRun.trace),
            selectinload(ReviewRun.repository),
        )
        .where(ReviewRun.id == run.id)
    )
    loaded_run = res.scalar_one()

    assert loaded_run.status == "success"
    assert len(loaded_run.findings) == 1
    assert loaded_run.findings[0].title == "SQL injection risk"
    assert loaded_run.findings[0].is_reproduced is True
    assert loaded_run.findings[0].precedent_citation == "CVE-2024-0001"
    assert loaded_run.trace is not None
    assert loaded_run.trace.trace_id == "trace_test_123"
    assert loaded_run.trace.prompt_tokens == 3000
    assert loaded_run.repository.full_name == "org-trace/repo"


@pytest.mark.asyncio
async def test_repo_precedents_relationship(session: AsyncSession) -> None:
    tenant = Tenant(github_org_id="org-prec", name="Precedent Org")
    session.add(tenant)
    await session.flush()

    repo = Repository(tenant_id=tenant.id, full_name="org-prec/service")
    session.add(repo)
    await session.flush()

    precedent = RepoPrecedent(
        repository_id=repo.id,
        citation="PR #102",
        title="Sanitize auth header on redirect",
        description="Never forward auth tokens across domains",
        file_pattern="src/http/*.py",
        code_snippet="if domain != orig: headers.pop('Auth')",
    )
    session.add(precedent)
    await session.commit()

    res = await session.execute(
        select(Repository)
        .options(selectinload(Repository.precedents))
        .where(Repository.id == repo.id)
    )
    loaded_repo = res.scalar_one()
    assert len(loaded_repo.precedents) == 1
    assert loaded_repo.precedents[0].citation == "PR #102"
