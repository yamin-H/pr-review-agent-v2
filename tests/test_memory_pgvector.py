"""Unit and integration tests for TextEmbedder, semantic MemoryStore, and PgVectorMemoryStore."""

import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from review.db.models import Base, Repository, Tenant
from review.memory.models import Precedent
from review.memory.store import (
    MemoryStore,
    PgVectorMemoryStore,
    TextEmbedder,
)


def test_text_embedder_properties() -> None:
    """Verify deterministic unit vector properties and cosine similarity bounds."""
    embedder = TextEmbedder()
    v1 = embedder.embed("SQL injection vulnerability in user login authentication")
    v2 = embedder.embed("SQL injection vulnerability in user login authentication")
    v3 = embedder.embed("SQL injection attack during user authentication login")
    v4 = embedder.embed("Fix CSS flexbox styling and alignment on navbar buttons")

    # Dimensions
    assert len(v1) == 384
    assert len(v3) == 384

    # Determinism
    assert v1 == v2

    # Unit norm: sum(v^2) == 1.0
    norm = sum(x * x for x in v1)
    assert abs(norm - 1.0) < 1e-4

    # Identical texts yield cosine similarity 1.0
    sim_identical = embedder.cosine_similarity(v1, v2)
    assert abs(sim_identical - 1.0) < 1e-4
    assert abs(embedder.cosine_distance(v1, v2)) < 1e-4

    # Semantically related texts have high similarity
    sim_related = embedder.cosine_similarity(v1, v3)
    assert sim_related > 0.40

    # Unrelated texts have low similarity
    sim_unrelated = embedder.cosine_similarity(v1, v4)
    assert sim_unrelated < sim_related


def test_memory_store_deduplication_and_hybrid_search() -> None:
    """Verify in-memory SQLite store with vector deduplication and hybrid search."""
    store = MemoryStore(db_path=":memory:")

    p1 = Precedent(
        id="prec_auth_sqli_1",
        repo="octocat/hello-world",
        title="SQL injection in auth login route",
        description="User login form concatenates unsanitized query parameters directly.",
        citation="PR #101",
        file_pattern="src/auth/*.py",
    )
    saved1, is_new1 = store.deduplicate_and_add(p1)
    assert is_new1 is True
    assert saved1.id == "prec_auth_sqli_1"

    # Near duplicate precedent
    p2 = Precedent(
        id="prec_auth_sqli_2",
        repo="octocat/hello-world",
        title="SQL injection in auth login route",
        description="User login form concatenates unsanitized query parameters directly.",
        citation="CVE-2023-99999",
        file_pattern="src/auth/*.py",
    )
    saved2, is_new2 = store.deduplicate_and_add(p2, threshold=0.80)
    assert is_new2 is False
    assert saved2.id == "prec_auth_sqli_1"
    assert "CVE-2023-99999" in saved2.citation
    assert "PR #101" in saved2.citation

    # Search with semantic similarity
    results = store.search_precedents(
        query="Vulnerable SQL query parameter concatenation in login form",
        repo="octocat/hello-world",
        min_score=0.2,
    )
    assert len(results) >= 1
    assert results[0].precedent.id == "prec_auth_sqli_1"
    assert results[0].score > 0.3


@pytest.mark.asyncio
async def test_pgvector_memory_store_sqlite_engine() -> None:
    """Verify PgVectorMemoryStore against SQLite engine simulating cross-dialect compatibility."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        # Seed Tenant & Repo
        tenant = Tenant(id="tenant-1", github_org_id="testorg", name="Test Org")
        repo = Repository(
            id="repo-1",
            tenant_id="tenant-1",
            full_name="testorg/web-service",
            github_repo_id=1234,
        )
        session.add_all([tenant, repo])
        await session.commit()

        store = PgVectorMemoryStore(session=session)

        # Ingest precedent
        prec1 = Precedent(
            id="prec_sqli_db",
            repo="testorg/web-service",
            title="Raw SQL execution without parameter binding",
            description="Calls to db.execute must use bind params to avoid SQL injection.",
            citation="PR #42",
            file_pattern="db/*.py",
        )
        saved, is_new = await store.deduplicate_and_add(prec1)
        assert is_new is True

        # Fetch and verify
        retrieved = await store.get_precedent("prec_sqli_db")
        assert retrieved is not None
        assert retrieved.title == prec1.title
        assert retrieved.embedding is not None

        # Duplicate check
        prec2 = Precedent(
            id="prec_sqli_dup",
            repo="testorg/web-service",
            title="Raw SQL execution without parameter binding",
            description="Calls to db.execute must use bind params to avoid SQL injection.",
            citation="Issue #99",
            file_pattern="db/*.py",
        )
        merged, is_new_2 = await store.deduplicate_and_add(prec2, threshold=0.85)
        assert is_new_2 is False
        assert merged.id == "prec_sqli_db"
        assert "Issue #99" in merged.citation

        # Semantic Search
        search_res = await store.search_precedents(
            query="parameter binding raw sql db.execute",
            repo="testorg/web-service",
            min_score=0.1,
        )
        assert len(search_res) >= 1
        assert search_res[0].precedent.id == "prec_sqli_db"

    await engine.dispose()


@pytest.mark.asyncio
async def test_pgvector_memory_store_live_postgres() -> None:
    """Verify PgVectorMemoryStore against live PostgreSQL container with HNSW index."""
    import uuid

    postgres_dsn = os.getenv(
        "TEST_POSTGRES_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/review_agent"
    )
    try:
        engine = create_async_engine(postgres_dsn, echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(lambda _: None)
    except Exception:
        pytest.skip("Live PostgreSQL container not reachable; skipping live pgvector test.")

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    rand_suffix = uuid.uuid4().hex[:8]
    tenant_id = f"tenant-pg-{rand_suffix}"
    repo_id = f"repo-pg-{rand_suffix}"
    org_id = f"pgorg-{rand_suffix}"
    full_name = f"{org_id}/crypto-wallet"
    prec_id = f"prec_cve_{rand_suffix}"

    async with session_maker() as session:
        tenant = Tenant(id=tenant_id, github_org_id=org_id, name="Pg Org")
        repo = Repository(
            id=repo_id,
            tenant_id=tenant_id,
            full_name=full_name,
            github_repo_id=9876,
        )
        session.add_all([tenant, repo])
        await session.commit()

        try:
            store = PgVectorMemoryStore(session=session)

            # Ingest precedent with pgvector embedding
            p_cve = Precedent(
                id=prec_id,
                repo=full_name,
                title="Reentrancy vulnerability in withdrawal logic",
                description="State changes must occur before external token transfer calls.",
                citation="CVE-2024-55555",
                file_pattern="contracts/*.sol",
            )
            saved, is_new = await store.deduplicate_and_add(p_cve, repo_id=repo_id)
            assert is_new is True
            await session.commit()

            # Native HNSW cosine distance search
            search_res = await store.search_precedents(
                query="Reentrancy vulnerability in withdrawal logic with external token calls",
                repo=full_name,
                min_score=0.1,
                limit=3,
            )
            assert len(search_res) >= 1
            assert search_res[0].precedent.id == prec_id
            assert search_res[0].score > 0.4
        finally:
            # Clean up
            await session.rollback()
            try:
                await session.execute(
                    Base.metadata.tables["repo_precedents"].delete().where(
                        Base.metadata.tables["repo_precedents"].c.id == prec_id
                    )
                )
                await session.execute(
                    Base.metadata.tables["repositories"].delete().where(
                        Base.metadata.tables["repositories"].c.id == repo_id
                    )
                )
                await session.execute(
                    Base.metadata.tables["tenants"].delete().where(
                        Base.metadata.tables["tenants"].c.id == tenant_id
                    )
                )
                await session.commit()
            except Exception:
                pass

    await engine.dispose()
