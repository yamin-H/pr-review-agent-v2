"""GitHub App authentication and RS256 JWT installation token acquisition."""

import logging
import time

import httpx
import jwt

logger = logging.getLogger("review.github.app_auth")

GITHUB_API_BASE = "https://api.github.com"
JWT_EXPIRATION_SECONDS = 600  # 10 minutes maximum allowed by GitHub
TOKEN_REFRESH_BUFFER_SECONDS = 300  # Refresh 5 minutes before expiry


class GitHubAuthError(Exception):
    """Raised when GitHub App authentication or token exchange fails."""


class GitHubAppAuth:
    """Manages GitHub App RS256 JWT generation and installation token acquisition."""

    def __init__(self, app_id: str, private_key_pem: str) -> None:
        self.app_id = app_id
        self.private_key_pem = private_key_pem
        self._cached_tokens: dict[int, tuple[str, float]] = {}  # installation_id -> (token, expiry)

    def generate_jwt(self) -> str:
        """Create a signed RS256 JWT authenticating as the GitHub App."""
        if not self.app_id or not self.private_key_pem:
            raise GitHubAuthError("Cannot generate JWT: app_id or private_key_pem is missing.")

        now = int(time.time())
        payload = {
            "iat": now - 60,  # 60 seconds in the past to allow clock drift
            "exp": now + JWT_EXPIRATION_SECONDS,
            "iss": self.app_id,
        }

        try:
            token = jwt.encode(payload, self.private_key_pem, algorithm="RS256")
            return str(token)
        except Exception as e:
            raise GitHubAuthError(f"Failed to sign GitHub App JWT: {e}") from e

    def get_installation_token(
        self,
        installation_id: int,
        client: httpx.Client | None = None,
    ) -> str:
        """Exchange App JWT for an installation access token, with caching."""
        now = time.time()
        cached = self._cached_tokens.get(installation_id)
        if cached:
            token, expiry = cached
            if now < (expiry - TOKEN_REFRESH_BUFFER_SECONDS):
                return token

        app_jwt = self.generate_jwt()
        headers = {
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Autonomous-PR-Review-Agent",
        }

        url = f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens"

        def _do_request(http_client: httpx.Client) -> str:
            res = http_client.post(url, headers=headers, timeout=15.0)
            if res.status_code != 201:
                raise GitHubAuthError(
                    f"Token exchange failed ({res.status_code}): {res.text}"
                )
            data = res.json()
            access_token = str(data["token"])
            # Default GitHub installation tokens last 1 hour
            self._cached_tokens[installation_id] = (access_token, now + 3600)
            return access_token

        if client is not None:
            return _do_request(client)

        with httpx.Client() as http_client:
            return _do_request(http_client)


__all__ = ["GitHubAppAuth", "GitHubAuthError"]
