"""Webhook security, HMAC signature verification, and delivery deduplication."""

import hashlib
import hmac
import logging
import time

logger = logging.getLogger("review.service.security")

DEFAULT_DEDUPLICATION_TTL_SECONDS = 3600  # 1 hour
MAX_DELIVERY_CACHE_SIZE = 10_000


def verify_github_signature(
    payload_bytes: bytes,
    signature_header: str | None,
    secret: str,
) -> bool:
    """Verify that a webhook payload was genuinely signed by GitHub using constant-time HMAC.

    Args:
        payload_bytes: The exact raw bytes of the incoming request body.
        signature_header: The value of the 'X-Hub-Signature-256' header.
        secret: The configured webhook secret.

    Returns:
        True if signature is valid, False otherwise.
    """
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

    # Use constant-time comparison to prevent timing attack vulnerabilities
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
        """Evict expired delivery IDs or prune oldest if capacity is exceeded."""
        expired = [
            delivery_id
            for delivery_id, timestamp in self._seen.items()
            if now - timestamp > self.ttl_seconds
        ]
        for delivery_id in expired:
            del self._seen[delivery_id]

        if len(self._seen) > self.max_entries:
            # Drop oldest entries if still exceeding limit
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
