"""Unit tests for GitHub App authentication and REST API client."""

import json
from unittest.mock import MagicMock

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from review.github.client import (
    GitHubAPIError,
    GitHubAppAuth,
    GitHubAuthError,
    GitHubClient,
)


def _generate_rsa_key_pair() -> tuple[str, str]:
    """Generate real in-memory RSA key pair for testing RS256 token generation."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem


def test_github_app_jwt_generation_valid() -> None:
    private_pem, public_pem = _generate_rsa_key_pair()
    auth = GitHubAppAuth(app_id="12345", private_key_pem=private_pem)

    token = auth.generate_jwt()
    assert token is not None

    # Verify and decode with public key
    decoded = jwt.decode(token, public_pem, algorithms=["RS256"])
    assert decoded["iss"] == "12345"
    assert "iat" in decoded
    assert "exp" in decoded
    assert decoded["exp"] > decoded["iat"]


def test_github_app_jwt_generation_missing_credentials() -> None:
    auth_empty = GitHubAppAuth(app_id="", private_key_pem="")
    with pytest.raises(GitHubAuthError):
        auth_empty.generate_jwt()


def test_github_app_token_exchange_caching() -> None:
    private_pem, _ = _generate_rsa_key_pair()
    auth = GitHubAppAuth(app_id="12345", private_key_pem=private_pem)

    mock_http = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.json.return_value = {"token": "ghs_installation_token_abc"}
    mock_http.post.return_value = mock_response

    # First fetch: calls exchange API
    token1 = auth.get_installation_token(installation_id=999, client=mock_http)
    assert token1 == "ghs_installation_token_abc"
    assert mock_http.post.call_count == 1

    # Second fetch: uses cached token
    token2 = auth.get_installation_token(installation_id=999, client=mock_http)
    assert token2 == "ghs_installation_token_abc"
    assert mock_http.post.call_count == 1  # Still 1 (cached!)


def test_github_client_get_pull_request_diff() -> None:
    mock_http = MagicMock()
    mock_res = MagicMock()
    mock_res.status_code = 200
    mock_res.text = "diff --git a/test.py b/test.py\n+print('hello')"
    mock_http.request.return_value = mock_res

    client = GitHubClient(token="ghs_test", http_client=mock_http)
    diff = client.get_pull_request_diff(owner="acme", repo="app", pull_number=42)

    assert "+print('hello')" in diff
    mock_http.request.assert_called_once_with(
        method="GET",
        url="https://api.github.com/repos/acme/app/pulls/42",
        headers={
            "Authorization": "token ghs_test",
            "Accept": "application/vnd.github.v3.diff",
            "User-Agent": "Autonomous-PR-Review-Agent",
        },
        json=None,
        timeout=30.0,
    )


def test_github_client_post_review_governance_override() -> None:
    mock_http = MagicMock()
    mock_res = MagicMock()
    mock_res.status_code = 200
    mock_res.json.return_value = {"id": 1001, "state": "COMMENTED"}
    mock_http.request.return_value = mock_res

    client = GitHubClient(token="ghs_test", http_client=mock_http)

    # Attempt to request changes or approve (which non-governance strictly forbids)
    result = client.post_review(
        owner="acme",
        repo="app",
        pull_number=42,
        commit_id="sha123",
        body="Review summary",
        comments=[{"path": "app.py", "line": 10, "body": "Fix this"}],
        event="APPROVE",  # Forbidden: should be overridden to COMMENT
    )

    assert result["id"] == 1001
    call_args = mock_http.request.call_args
    sent_payload = call_args[1]["json"]
    # Verify strict governance guardrail forced event to COMMENT
    assert sent_payload["event"] == "COMMENT"


def test_github_client_api_error_raised() -> None:
    mock_http = MagicMock()
    mock_res = MagicMock()
    mock_res.status_code = 404
    mock_res.text = json.dumps({"message": "Not Found"})
    mock_http.request.return_value = mock_res

    client = GitHubClient(token="ghs_test", http_client=mock_http)
    with pytest.raises(GitHubAPIError):
        client.get_pull_request_metadata("acme", "app", 9999)
