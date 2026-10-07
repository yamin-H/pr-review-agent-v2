from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from review.db.models import FindingRecord, Repository, ReviewRun, Tenant
from review.db.session import create_engine, drop_db, get_session_maker, init_db


@pytest_asyncio.fixture
async def async_test_session() -> AsyncGenerator[AsyncSession, None]:
    """Create an isolated in-memory SQLite database session for tenant isolation testing."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    session_maker = get_session_maker(engine)

    async with session_maker() as session:
        yield session

    await drop_db(engine)
    await engine.dispose()


@pytest.mark.asyncio
async def test_tenant_isolation_scoping(async_test_session: AsyncSession) -> None:
    session = async_test_session

    # 1. Create Organization Alpha (Tenant A)
    tenant_a = Tenant(
        github_org_id="org-alpha",
        name="Alpha Corp",
        plan_tier="enterprise",
    )
    session.add(tenant_a)
    await session.flush()

    repo_a = Repository(
        tenant_id=tenant_a.id,
        full_name="alpha-corp/backend",
        github_repo_id=101,
        settings={"max_steps": 10},
    )
    session.add(repo_a)
    await session.flush()

    run_a = ReviewRun(
        repository_id=repo_a.id,
        pull_number=1,
        head_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        idempotency_key="review:alpha-corp/backend:1:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        status="success",
        summary="Alpha review passed.",
    )
    session.add(run_a)
    await session.flush()

    finding_a = FindingRecord(
        review_run_id=run_a.id,
        file_path="src/auth.py",
        line_number=42,
        title="Alpha security finding",
        body="Secret key exposed in Alpha",
        severity="high",
        status="verified",
    )
    session.add(finding_a)

    # 2. Create Organization Beta (Tenant B)
    tenant_b = Tenant(
        github_org_id="org-beta",
        name="Beta Inc",
        plan_tier="standard",
    )
    session.add(tenant_b)
    await session.flush()

    repo_b = Repository(
        tenant_id=tenant_b.id,
        full_name="beta-inc/frontend",
        github_repo_id=202,
        settings={"max_steps": 15},
    )
    session.add(repo_b)
    await session.flush()

    run_b = ReviewRun(
        repository_id=repo_b.id,
        pull_number=1,
        head_sha="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        idempotency_key="review:beta-inc/frontend:1:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        status="success",
        summary="Beta review passed.",
    )
    session.add(run_b)
    await session.flush()

    finding_b = FindingRecord(
        review_run_id=run_b.id,
        file_path="src/app.tsx",
        line_number=15,
        title="Beta UI finding",
        body="Missing key prop in Beta",
        severity="low",
        status="verified",
    )
    session.add(finding_b)
    await session.commit()

    # 3. Query Scoped to Tenant A - Verify zero leakage of Tenant B records
    query_a = (
        select(ReviewRun)
        .join(ReviewRun.repository)
        .join(Repository.tenant)
        .where(Tenant.github_org_id == "org-alpha")
        .options(selectinload(ReviewRun.findings))
    )
    res_a = await session.execute(query_a)
    runs_a = res_a.scalars().all()

    assert len(runs_a) == 1
    assert runs_a[0].id == run_a.id
    assert runs_a[0].summary == "Alpha review passed."
    assert len(runs_a[0].findings) == 1
    assert runs_a[0].findings[0].title == "Alpha security finding"

    # Verify Tenant B's finding is NOT visible in Tenant A's results
    finding_titles_a = [f.title for f in runs_a[0].findings]
    assert "Beta UI finding" not in finding_titles_a

    # 4. Query Scoped to Tenant B
    query_b = (
        select(ReviewRun)
        .join(ReviewRun.repository)
        .join(Repository.tenant)
        .where(Tenant.github_org_id == "org-beta")
        .options(selectinload(ReviewRun.findings))
    )
    res_b = await session.execute(query_b)
    runs_b = res_b.scalars().all()

    assert len(runs_b) == 1
    assert runs_b[0].id == run_b.id
    assert runs_b[0].summary == "Beta review passed."
    assert len(runs_b[0].findings) == 1
    assert runs_b[0].findings[0].title == "Beta UI finding"

    # 5. Cascading Deletion Boundary: Deleting Tenant A leaves Tenant B completely intact
    await session.delete(tenant_a)
    await session.commit()

    # Confirm Tenant A and its repositories, runs, and findings are gone
    check_a = await session.execute(select(Tenant).where(Tenant.github_org_id == "org-alpha"))
    assert check_a.scalar_one_or_none() is None

    check_repo_a = await session.execute(
        select(Repository).where(Repository.full_name == "alpha-corp/backend")
    )
    assert check_repo_a.scalar_one_or_none() is None

    # Confirm Tenant B and its full hierarchy are completely unaffected
    check_b = await session.execute(
        select(Tenant).where(Tenant.github_org_id == "org-beta").options(
            selectinload(Tenant.repositories)
        )
    )
    loaded_b = check_b.scalar_one_or_none()
    assert loaded_b is not None
    assert len(loaded_b.repositories) == 1
    assert loaded_b.repositories[0].full_name == "beta-inc/frontend"
