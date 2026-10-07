"""GitHub App authentication and REST API client for pull requests and reviews."""

import base64
import logging
import time
from typing import Any

import httpx
import jwt

logger = logging.getLogger("review.github.client")

GITHUB_API_BASE = "https://api.github.com"
JWT_EXPIRATION_SECONDS = 600  # 10 minutes (GitHub maximum is 10 minutes)
TOKEN_REFRESH_BUFFER_SECONDS = 300  # Refresh 5 minutes before expiry


class GitHubAuthError(Exception):
    """Raised when GitHub App authentication or token exchange fails."""


class GitHubAPIError(Exception):
    """Raised when GitHub API returns an error response."""


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
            # Default GitHub tokens last 1 hour
            self._cached_tokens[installation_id] = (access_token, now + 3600)
            return access_token

        if client is not None:
            return _do_request(client)

        with httpx.Client() as http_client:
            return _do_request(http_client)


class GitHubClient:
    """Client for GitHub REST API operations on pull requests and review comments."""

    def __init__(
        self,
        token: str,
        base_url: str = GITHUB_API_BASE,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self._external_client = http_client

    def _get_headers(self, accept: str = "application/vnd.github.v3+json") -> dict[str, str]:
        return {
            "Authorization": f"token {self.token}",
            "Accept": accept,
            "User-Agent": "Autonomous-PR-Review-Agent",
        }

    def _request(
        self,
        method: str,
        path: str,
        accept: str = "application/vnd.github.v3+json",
        json_data: Any | None = None,
    ) -> httpx.Response:
        url = f"{self.base_url}{path}"
        headers = self._get_headers(accept=accept)

        if self._external_client is not None:
            res = self._external_client.request(
                method=method,
                url=url,
                headers=headers,
                json=json_data,
                timeout=30.0,
            )
        else:
            with httpx.Client() as client:
                res = client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=json_data,
                    timeout=30.0,
                )

        if res.status_code >= 400:
            raise GitHubAPIError(
                f"GitHub API {method} {path} error ({res.status_code}): {res.text}"
            )

        return res

    def get_pull_request_diff(self, owner: str, repo: str, pull_number: int) -> str:
        """Fetch the unified diff of a pull request."""
        path = f"/repos/{owner}/{repo}/pulls/{pull_number}"
        res = self._request(
            method="GET",
            path=path,
            accept="application/vnd.github.v3.diff",
        )
        return res.text

    def get_pull_request_metadata(
        self,
        owner: str,
        repo: str,
        pull_number: int,
    ) -> dict[str, Any]:
        """Fetch PR metadata including title, description, head and base commits."""
        path = f"/repos/{owner}/{repo}/pulls/{pull_number}"
        res = self._request(method="GET", path=path)
        data: dict[str, Any] = res.json()
        return data

    def get_repo_file(
        self,
        owner: str,
        repo: str,
        file_path: str,
        ref: str = "HEAD",
    ) -> str | None:
        """Fetch file contents from repository at ref (e.g. .reviewer.yml)."""
        path = f"/repos/{owner}/{repo}/contents/{file_path.lstrip('/')}?ref={ref}"
        try:
            res = self._request(method="GET", path=path)
            data = res.json()
            if "content" in data:
                content_bytes = base64.b64decode(data["content"])
                return content_bytes.decode("utf-8", errors="replace")
            return None
        except GitHubAPIError as e:
            if "404" in str(e):
                return None
            raise

    def post_review(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        commit_id: str,
        body: str,
        comments: list[dict[str, Any]],
        event: str = "COMMENT",
    ) -> dict[str, Any]:
        """Submit a formal Pull Request Review with top-level summary and inline comments.

        Security Governance Guardrail:
        Strictly enforces 'COMMENT' events. Never approves or requests changes.
        """
        if event != "COMMENT":
            logger.warning(
                "Governance override: requested event '%s' overridden to 'COMMENT'.",
                event,
            )
            event = "COMMENT"

        path = f"/repos/{owner}/{repo}/pulls/{pull_number}/reviews"
        payload = {
            "commit_id": commit_id,
            "body": body,
            "event": "COMMENT",
            "comments": comments,
        }

        res = self._request(method="POST", path=path, json_data=payload)
        data: dict[str, Any] = res.json()
        return data
