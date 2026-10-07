"""GitHub Webhook security verification, HMAC checking, and replay deduplication."""

import hashlib
import hmac
import logging
import time
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger("review.github.webhooks")

DEFAULT_DEDUPLICATION_TTL_SECONDS = 3600  # 1 hour
MAX_DELIVERY_CACHE_SIZE = 10_000


def verify_github_signature(
    payload_bytes: bytes,
    signature_header: str | None,
    secret: str,
) -> bool:
    """Verify webhook payload was signed by GitHub using constant-time HMAC-SHA256."""
    if not secret:
        logger.error("Webhook verification failed: secret is empty or not configured.")
        return False

    if not signature_header or not signature_header.startswith("sha256="):
        logger.warning("Missing or malformed X-Hub-Signature-256 header: %s", signature_header)
        return False

    computed_digest = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload_bytes,
        digestmod=hashlib.sha256,
    ).hexdigest()

    expected_signature = f"sha256={computed_digest}"
    return hmac.compare_digest(expected_signature, signature_header)


class DeliveryDeduplicator:
    """Replay defense tracker that deduplicates X-GitHub-Delivery IDs with automatic expiration."""

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_DEDUPLICATION_TTL_SECONDS,
        max_entries: int = MAX_DELIVERY_CACHE_SIZE,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._seen: dict[str, float] = {}

    def _cleanup(self, now: float) -> None:
        expired = [
            delivery_id
            for delivery_id, timestamp in self._seen.items()
            if now - timestamp > self.ttl_seconds
        ]
        for delivery_id in expired:
            del self._seen[delivery_id]

        if len(self._seen) > self.max_entries:
            sorted_entries = sorted(self._seen.items(), key=lambda item: item[1])
            excess = len(self._seen) - self.max_entries
            for delivery_id, _ in sorted_entries[:excess]:
                del self._seen[delivery_id]

    def is_duplicate(self, delivery_id: str) -> bool:
        """Check if delivery ID has been seen. Records it if new."""
        if not delivery_id:
            return False

        now = time.time()
        self._cleanup(now)

        if delivery_id in self._seen:
            logger.warning("Duplicate webhook delivery detected and dropped: %s", delivery_id)
            return True

        self._seen[delivery_id] = now
        return False


class PullRequestWebhookPayload(BaseModel):
    """Parsed metadata from a GitHub pull_request webhook payload."""

    action: str
    delivery_id: str = Field(default="")
    installation_id: int = Field(default=0)
    owner: str = Field(default="")
    repo: str = Field(default="")
    pull_number: int = Field(default=0)
    head_sha: str = Field(default="")
    base_sha: str = Field(default="")
    title: str = Field(default="")
    body: str = Field(default="")

    @classmethod
    def from_raw_payload(
        cls,
        payload: dict[str, Any],
        delivery_id: str = "",
    ) -> "PullRequestWebhookPayload":
        """Extract structured PR metadata from raw GitHub webhook JSON dictionary."""
        pr = payload.get("pull_request", {})
        repo = payload.get("repository", {})
        installation = payload.get("installation", {})

        return cls(
            action=payload.get("action", ""),
            delivery_id=delivery_id,
            installation_id=int(installation.get("id", 0)),
            owner=repo.get("owner", {}).get("login", ""),
            repo=repo.get("name", ""),
            pull_number=int(pr.get("number", 0)),
            head_sha=pr.get("head", {}).get("sha", ""),
            base_sha=pr.get("base", {}).get("sha", ""),
            title=pr.get("title", ""),
            body=pr.get("body", "") or "",
        )


__all__ = [
    "DeliveryDeduplicator",
    "PullRequestWebhookPayload",
    "verify_github_signature",
]
