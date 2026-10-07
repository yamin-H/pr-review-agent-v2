"""Authentication and webhook signature verification dependencies."""

import logging
from typing import Annotated

from fastapi import Header, HTTPException, Request, status

from review.api.deps import get_service_config
from review.github.webhooks import verify_github_signature
from review.service.config import ServiceConfig

logger = logging.getLogger("review.api.auth")


async def verify_webhook_hmac(
    request: Request,
    x_hub_signature_256: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
) -> bytes:
    """FastAPI dependency validating the GitHub HMAC-SHA256 signature against request body."""
    raw_body = await request.body()
    config: ServiceConfig = get_service_config()

    if config.webhook_secret:
        valid = verify_github_signature(
            payload_bytes=raw_body,
            signature_header=x_hub_signature_256,
            secret=config.webhook_secret,
        )
        if not valid:
            logger.warning("Rejected webhook: invalid HMAC signature.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid HMAC signature.",
            )

    return raw_body
