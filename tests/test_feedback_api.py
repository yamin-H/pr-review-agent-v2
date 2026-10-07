"""Tests for developer feedback, calibration, and precedent API endpoints and webhook ingestion."""

import hashlib
import hmac
import json
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from review.api.deps import get_db_session
from review.api.main import create_app
from review.db.models import Repository, Tenant
from review.db.session import create_engine, drop_db, get_session_maker, init_db


@pytest_asyncio.fixture
async def feedback_test_app() -> AsyncGenerator[tuple[TestClient, str, str], None]:
    """Provide a configured TestClient with pre-seeded tenant, repository, and session override."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    maker = get_session_maker(engine)

    repo_id = "repo-feedback-test"
    full_name = "testorg/feed-repo"

    async with maker() as s:
        tenant = Tenant(id="tenant-fb", github_org_id="testorg", name="Test Org")
        repo = Repository(
            id=repo_id,
            tenant_id="tenant-fb",
            full_name=full_name,
            github_repo_id=777,
        )
        s.add_all([tenant, repo])
        await s.commit()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db_session] = override_get_db
    client = TestClient(app)

    yield client, repo_id, full_name

    await drop_db(engine)
    await engine.dispose()


def test_submit_feedback_and_get_calibration(
    feedback_test_app: tuple[TestClient, str, str],
) -> None:
    """Verify POST feedback records signals and GET /calibration exposes rates."""
    client, repo_id, _ = feedback_test_app

    # 1. Submit positive reaction feedback
    payload_pos = {
        "pull_number": 42,
        "reaction": "+1",
        "action": "reacted",
        "rule_id": "security-csrf",
        "rule_category": "security",
        "user_login": "alice",
    }
    res = client.post(f"/api/v1/repos/{repo_id}/feedback", json=payload_pos)
    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "recorded"
    assert len(data["metrics_updated"]) >= 1

    # 2. Submit negative dismissals to trigger silence for 'noisy-rule'
    for i in range(3):
        payload_neg = {
            "pull_number": 50 + i,
            "reaction": "-1",
            "action": "dismissed",
            "rule_id": "noisy-rule",
            "rule_category": "style",
            "user_login": "bob",
        }
        res_neg = client.post(f"/api/v1/repos/{repo_id}/feedback", json=payload_neg)
        assert res_neg.status_code == 201

    # 3. GET /calibration
    calib_res = client.get(f"/api/v1/repos/{repo_id}/calibration")
    assert calib_res.status_code == 200
    metrics = calib_res.json()
    assert len(metrics) >= 1

    noisy_metric = next((m for m in metrics if m["target_key"] == "noisy-rule"), None)
    assert noisy_metric is not None
    assert noisy_metric["acceptance_rate"] == 0.0
    assert noisy_metric["silenced"] is True


def test_precedents_api_crud_and_search(
    feedback_test_app: tuple[TestClient, str, str],
) -> None:
    """Verify POST and GET precedents with vector search and deduplication."""
    client, repo_id, full_name = feedback_test_app

    # 1. Ingest precedent
    prec_in = {
        "title": "Use safe JSON deserialization",
        "description": "Never use pickle or raw eval on untrusted network packets.",
        "citation": "CVE-2024-1234",
        "file_pattern": "net/*.py",
        "category": "security",
    }
    create_res = client.post(f"/api/v1/repos/{repo_id}/precedents", json=prec_in)
    assert create_res.status_code == 201
    assert create_res.json()["status"] == "created"

    # 2. Ingest duplicate precedent
    prec_dup = {
        "title": "Use safe JSON deserialization",
        "description": "Never use pickle or raw eval on untrusted network packets.",
        "citation": "PR #888",
        "file_pattern": "net/*.py",
        "category": "security",
    }
    dup_res = client.post(f"/api/v1/repos/{repo_id}/precedents", json=prec_dup)
    assert dup_res.status_code == 201
    assert dup_res.json()["status"] == "merged"
    assert "PR #888" in dup_res.json()["precedent"]["citation"]

    # 3. Semantic search precedents
    search_res = client.get(
        f"/api/v1/repos/{repo_id}/precedents",
        params={"query": "safe deserialization untrusted pickle packets", "limit": 3},
    )
    assert search_res.status_code == 200
    results = search_res.json()
    assert len(results) >= 1
    assert results[0]["precedent"]["title"] == "Use safe JSON deserialization"
    assert results[0]["score"] > 0.2


def test_webhook_reaction_and_comment_signal_ingestion(
    feedback_test_app: tuple[TestClient, str, str],
) -> None:
    """Verify GitHub webhook endpoint ingests comment reaction signals."""
    client, repo_id, full_name = feedback_test_app
    secret = "test-webhook-secret"

    payload = {
        "action": "created",
        "repository": {"full_name": full_name, "id": 777},
        "pull_request": {"number": 12},
        "reaction": {"content": "+1"},
        "sender": {"login": "charlie"},
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    sig = "sha256=" + hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()

    headers = {
        "X-GitHub-Event": "reaction",
        "X-GitHub-Delivery": "deliv-react-1",
        "X-Hub-Signature-256": sig,
        "Content-Type": "application/json",
    }

    # Set secret in config
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GITHUB_WEBHOOK_SECRET", secret)
        res = client.post("/api/v1/github/webhooks", content=body_bytes, headers=headers)
        assert res.status_code == 200
        assert res.json()["status"] == "recorded"
