"""Unit tests for webhook security, HMAC signature verification, and replay defense."""

import hashlib
import hmac
import time

from review.service.security import DeliveryDeduplicator, verify_github_signature


def _sign(payload: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_verify_github_signature_valid() -> None:
    secret = "super_secret_webhook_key_123"
    payload = b'{"action": "opened", "pull_request": {"number": 42}}'
    sig = _sign(payload, secret)

    assert verify_github_signature(payload, sig, secret) is True


def test_verify_github_signature_tampered_payload() -> None:
    secret = "super_secret_webhook_key_123"
    payload = b'{"action": "opened", "pull_request": {"number": 42}}'
    sig = _sign(payload, secret)

    tampered_payload = b'{"action": "opened", "pull_request": {"number": 43}}'
    assert verify_github_signature(tampered_payload, sig, secret) is False


def test_verify_github_signature_wrong_secret() -> None:
    secret = "correct_secret"
    wrong_secret = "attacker_secret"
    payload = b'{"hello": "world"}'
    sig = _sign(payload, wrong_secret)

    assert verify_github_signature(payload, sig, secret) is False


def test_verify_github_signature_missing_or_malformed_header() -> None:
    secret = "my_secret"
    payload = b"{}"

    assert verify_github_signature(payload, None, secret) is False
    assert verify_github_signature(payload, "", secret) is False
    assert verify_github_signature(payload, "invalid_prefix_123", secret) is False


def test_verify_github_signature_empty_secret() -> None:
    payload = b"{}"
    sig = _sign(payload, "")
    assert verify_github_signature(payload, sig, "") is False


def test_delivery_deduplicator() -> None:
    dedup = DeliveryDeduplicator(ttl_seconds=1.0)
    deliv_id = "uuid-1234-abcd"

    # First delivery is allowed
    assert dedup.is_duplicate(deliv_id) is False

    # Second immediate delivery with same ID is detected as duplicate
    assert dedup.is_duplicate(deliv_id) is True

    # Different delivery ID is allowed
    assert dedup.is_duplicate("uuid-5678-efgh") is False

    # Empty delivery ID is handled gracefully
    assert dedup.is_duplicate("") is False

    # After TTL, original delivery ID expires and is allowed again
    time.sleep(1.1)
    assert dedup.is_duplicate(deliv_id) is False
