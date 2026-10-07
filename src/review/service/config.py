"""Service configuration and environment variable loading."""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


class ServiceConfig(BaseModel):
    """Runtime configuration for the GitHub App webhook and review service."""

    app_id: str = Field(
        default_factory=lambda: os.getenv("GITHUB_APP_ID", ""),
        description="GitHub App ID",
    )
    private_key: str = Field(
        default_factory=lambda: os.getenv("GITHUB_PRIVATE_KEY", ""),
        description="GitHub App RSA private key content (PEM formatted)",
    )
    private_key_path: Path | None = Field(
        default_factory=lambda: Path(p) if (p := os.getenv("GITHUB_PRIVATE_KEY_PATH")) else None,
        description="Optional path to private key PEM file",
    )
    webhook_secret: str = Field(
        default_factory=lambda: os.getenv("GITHUB_WEBHOOK_SECRET", ""),
        description="GitHub Webhook HMAC Secret",
    )
    host: str = Field(
        default_factory=lambda: os.getenv("SERVICE_HOST", "0.0.0.0"),
        description="API host binding",
    )
    port: int = Field(
        default_factory=lambda: int(os.getenv("SERVICE_PORT", "8000")),
        description="API port binding",
    )
    enable_verifier: bool = Field(
        default=True,
        description="Whether to run the adversarial verifier pass on candidate findings",
    )
    enable_reproduction: bool = Field(
        default=True,
        description="Whether to synthesize and execute reproduction tests in sandbox",
    )

    def get_private_key_pem(self) -> str:
        """Resolve private key content from inline environment variable or file path."""
        if self.private_key.strip():
            return self.private_key.strip()
        if self.private_key_path and self.private_key_path.exists():
            return self.private_key_path.read_text(encoding="utf-8").strip()
        return ""
