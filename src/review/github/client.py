"""GitHub REST API client for pull requests, reviews, and repository content."""

import base64
import contextlib
import logging
from typing import Any

import httpx

from review.github.app_auth import GITHUB_API_BASE, GitHubAppAuth, GitHubAuthError

logger = logging.getLogger("review.github.client")


class GitHubAPIError(Exception):
    """Raised when GitHub API returns an error response."""


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
        self._etag_cache: dict[str, tuple[str, str]] = {}  # url -> (etag, text_body)
        self.rate_limit_remaining: int | None = None
        self.rate_limit_reset: int | None = None

    def _get_headers(
        self, accept: str = "application/vnd.github.v3+json", etag: str | None = None
    ) -> dict[str, str]:
        headers = {
            "Authorization": f"token {self.token}",
            "Accept": accept,
            "User-Agent": "Autonomous-PR-Review-Agent",
        }
        if etag:
            headers["If-None-Match"] = etag
        return headers

    def _request(
        self,
        method: str,
        path: str,
        accept: str = "application/vnd.github.v3+json",
        json_data: Any | None = None,
        use_cache: bool = False,
    ) -> httpx.Response:
        url = f"{self.base_url}{path}"
        cached_entry = self._etag_cache.get(url) if (use_cache and method == "GET") else None
        etag = cached_entry[0] if cached_entry else None

        headers = self._get_headers(accept=accept, etag=etag)

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

        # Track rate limit metadata
        if "x-ratelimit-remaining" in res.headers:
            with contextlib.suppress(ValueError):
                self.rate_limit_remaining = int(res.headers["x-ratelimit-remaining"])
        if "x-ratelimit-reset" in res.headers:
            with contextlib.suppress(ValueError):
                self.rate_limit_reset = int(res.headers["x-ratelimit-reset"])

        # Handle 304 Not Modified
        if res.status_code == 304 and cached_entry:
            # Construct a synthetic response reusing cached body
            return httpx.Response(
                status_code=200,
                text=cached_entry[1],
                headers=res.headers,
                request=res.request,
            )

        if res.status_code >= 400:
            raise GitHubAPIError(
                f"GitHub API {method} {path} error ({res.status_code}): {res.text}"
            )

        # Update ETag cache on successful GET
        if use_cache and method == "GET" and res.status_code == 200:
            resp_etag = res.headers.get("etag")
            if resp_etag:
                self._etag_cache[url] = (resp_etag, res.text)

        return res

    def get_pull_request_diff(self, owner: str, repo: str, pull_number: int) -> str:
        """Fetch the unified diff of a pull request with ETag caching."""
        path = f"/repos/{owner}/{repo}/pulls/{pull_number}"
        res = self._request(
            method="GET",
            path=path,
            accept="application/vnd.github.v3.diff",
            use_cache=True,
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
        res = self._request(method="GET", path=path, use_cache=True)
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
            res = self._request(method="GET", path=path, use_cache=True)
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


__all__ = [
    "GITHUB_API_BASE",
    "GitHubAPIError",
    "GitHubAppAuth",
    "GitHubAuthError",
    "GitHubClient",
]
