"""Integration tests for FastAPI webhook receiver, authentication, and task dispatch."""

import hashlib
import hmac
import json
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from review.service.api import create_app
from review.service.config import ServiceConfig
from review.service.security import DeliveryDeduplicator


def _make_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_health_check_endpoint() -> None:
    app = create_app()
    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["service"] == "review-agent"


def test_webhook_missing_signature_rejected() -> None:
    config = ServiceConfig(webhook_secret="secret_abc_123")
    app = create_app(config=config)
    client = TestClient(app)

    payload = json.dumps({"action": "opened"}).encode("utf-8")
    response = client.post(
        "/api/v1/github/webhooks",
        content=payload,
        headers={"X-GitHub-Event": "pull_request"},
    )
    assert response.status_code == 401
    assert "Invalid HMAC signature" in response.json()["detail"]


def test_webhook_ping_event() -> None:
    secret = "test_webhook_secret"
    config = ServiceConfig(webhook_secret=secret)
    app = create_app(config=config)
    client = TestClient(app)

    payload = json.dumps({"zen": "Keep it logically awesome."}).encode("utf-8")
    sig = _make_signature(payload, secret)

    response = client.post(
        "/api/v1/github/webhooks",
        content=payload,
        headers={
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": sig,
            "X-GitHub-Delivery": "delivery-ping-1",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "pong"


def test_webhook_pull_request_opened_enqueues_task() -> None:
    secret = "test_webhook_secret"
    config = ServiceConfig(webhook_secret=secret)
    mock_worker = MagicMock()
    mock_worker.process_task = MagicMock()

    app = create_app(config=config, worker=mock_worker)
    client = TestClient(app)

    payload_dict = {
        "action": "opened",
        "installation": {"id": 12345},
        "repository": {
            "name": "my-repo",
            "owner": {"login": "my-org"},
        },
        "pull_request": {
            "number": 99,
            "title": "Fix memory leak",
            "body": "Detailed description",
            "head": {"sha": "fedcba987654"},
        },
    }
    payload_bytes = json.dumps(payload_dict).encode("utf-8")
    sig = _make_signature(payload_bytes, secret)

    response = client.post(
        "/api/v1/github/webhooks",
        content=payload_bytes,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": sig,
            "X-GitHub-Delivery": "delivery-pr-99",
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    assert response.json()["pull_number"] == 99
    assert response.json()["owner"] == "my-org"
    assert response.json()["repo"] == "my-repo"

    # Verify background task was scheduled
    mock_worker.process_task.assert_called_once()
    task = mock_worker.process_task.call_args[0][0]
    assert task.pull_number == 99
    assert task.owner == "my-org"
    assert task.repo == "my-repo"
    assert task.head_sha == "fedcba987654"
    assert task.delivery_id == "delivery-pr-99"


def test_webhook_duplicate_delivery_dropped() -> None:
    secret = "test_webhook_secret"
    config = ServiceConfig(webhook_secret=secret)
    deduplicator = DeliveryDeduplicator(ttl_seconds=60.0)
    mock_worker = MagicMock()

    app = create_app(config=config, worker=mock_worker, deduplicator=deduplicator)
    client = TestClient(app)

    payload_bytes = json.dumps({"action": "opened"}).encode("utf-8")
    sig = _make_signature(payload_bytes, secret)
    headers = {
        "X-GitHub-Event": "ping",
        "X-Hub-Signature-256": sig,
        "X-GitHub-Delivery": "delivery-duplicate-test",
    }

    # First delivery: processed
    res1 = client.post("/api/v1/github/webhooks", content=payload_bytes, headers=headers)
    assert res1.status_code == 200
    assert res1.json()["status"] == "pong"

    # Second delivery with identical delivery ID: dropped by replay defense
    res2 = client.post("/api/v1/github/webhooks", content=payload_bytes, headers=headers)
    assert res2.status_code == 200
    assert res2.json()["status"] == "ignored"
    assert res2.json()["reason"] == "duplicate_delivery"


def test_webhook_ignored_action() -> None:
    secret = "test_webhook_secret"
    config = ServiceConfig(webhook_secret=secret)
    app = create_app(config=config)
    client = TestClient(app)

    payload_bytes = json.dumps({"action": "labeled"}).encode("utf-8")
    sig = _make_signature(payload_bytes, secret)

    response = client.post(
        "/api/v1/github/webhooks",
        content=payload_bytes,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": sig,
            "X-GitHub-Delivery": "delivery-labeled",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert response.json()["action"] == "labeled"
