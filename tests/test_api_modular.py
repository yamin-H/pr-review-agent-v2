from collections.abc import AsyncGenerator, Callable
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from review.api.deps import get_db_session, get_redis_queue
from review.api.main import create_app
from review.db.models import Repository, ReviewRun, Tenant
from review.db.session import create_engine, drop_db, get_session_maker, init_db


@pytest_asyncio.fixture
async def api_test_db() -> AsyncGenerator[Callable[[], AsyncGenerator[AsyncSession, None]], None]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    maker = get_session_maker(engine)

    async with maker() as s:
        # Prepopulate sample tenant and repo
        tenant = Tenant(github_org_id="api-org", name="API Org")
        s.add(tenant)
        await s.flush()
        repo = Repository(tenant_id=tenant.id, full_name="api-org/api-repo", github_repo_id=111)
        s.add(repo)
        await s.flush()
        run = ReviewRun(
            repository_id=repo.id,
            pull_number=1,
            head_sha="sha999",
            idempotency_key="review:api-org/api-repo:1:sha999",
            status="success",
            summary="API test review passed.",
        )
        s.add(run)
        await s.commit()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with maker() as session:
            yield session

    yield override_get_db

    await drop_db(engine)
    await engine.dispose()


def test_api_health_check() -> None:
    app = create_app()
    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"


def test_api_metrics_endpoints() -> None:
    app = create_app()
    client = TestClient(app)

    # Prometheus exposition
    res_prom = client.get("/metrics")
    assert res_prom.status_code == 200
    assert "pr_reviews_total" in res_prom.text

    # JSON metrics summary
    res_json = client.get("/api/v1/metrics")
    assert res_json.status_code == 200
    assert "reviews" in res_json.json()


@pytest.mark.asyncio
async def test_api_list_and_get_reviews(
    api_test_db: Callable[[], AsyncGenerator[AsyncSession, None]],
) -> None:
    app = create_app()
    app.dependency_overrides[get_db_session] = api_test_db
    client = TestClient(app)

    # List reviews
    res = client.get("/api/v1/reviews?repo=api-org/api-repo")
    assert res.status_code == 200
    runs = res.json()
    assert len(runs) == 1
    assert runs[0]["pull_number"] == 1
    assert runs[0]["summary"] == "API test review passed."

    # Get single review detail
    run_id = runs[0]["id"]
    res_detail = client.get(f"/api/v1/reviews/{run_id}")
    assert res_detail.status_code == 200
    detail = res_detail.json()
    assert detail["id"] == run_id
    assert detail["repository"] == "api-org/api-repo"


@pytest.mark.asyncio
async def test_api_repos_endpoints(
    api_test_db: Callable[[], AsyncGenerator[AsyncSession, None]],
) -> None:
    app = create_app()
    app.dependency_overrides[get_db_session] = api_test_db
    client = TestClient(app)

    # List repos
    res = client.get("/api/v1/repos")
    assert res.status_code == 200
    repos = res.json()
    assert len(repos) >= 1
    assert repos[0]["full_name"] == "api-org/api-repo"

    # Get single repo
    res_repo = client.get("/api/v1/repos/api-org/api-repo")
    assert res_repo.status_code == 200
    assert res_repo.json()["full_name"] == "api-org/api-repo"

    # Update repo settings
    update_payload = {"is_active": False, "settings": {"max_steps": 25}}
    res_update = client.put("/api/v1/repos/api-org/api-repo/settings", json=update_payload)
    assert res_update.status_code == 200
    assert res_update.json()["is_active"] is False
    assert res_update.json()["settings"]["max_steps"] == 25


@pytest.mark.asyncio
async def test_webhook_enqueue_with_redis(
    api_test_db: Callable[[], AsyncGenerator[AsyncSession, None]],
) -> None:
    mock_redis = AsyncMock()
    mock_redis.enqueue_job = AsyncMock()

    app = create_app()
    app.dependency_overrides[get_db_session] = api_test_db
    app.dependency_overrides[get_redis_queue] = lambda: mock_redis
    client = TestClient(app)

    payload = {
        "action": "opened",
        "installation": {"id": 1234},
        "repository": {"name": "repo", "owner": {"login": "owner"}},
        "pull_request": {"number": 1, "head": {"sha": "sha_pr_1"}},
    }

    res = client.post(
        "/api/v1/github/webhooks",
        json=payload,
        headers={"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "del-1"},
    )
    assert res.status_code == 202
    assert res.json()["status"] == "accepted"
    mock_redis.enqueue_job.assert_awaited_once()
