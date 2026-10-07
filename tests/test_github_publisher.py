"""Unit tests for GitHub ReviewPublisher formatting and payload preparation."""

from unittest.mock import MagicMock

from review.findings import Finding, ReviewOutput, Severity
from review.github.publisher import (
    ReviewPublisher,
    format_inline_comment_body,
    format_top_level_summary,
)


def test_format_inline_comment_basic() -> None:
    f = Finding(
        file="src/app.py",
        line=42,
        title="Unchecked division by zero",
        body="Ensure denominator is non-zero before dividing.",
        severity=Severity.HIGH,
    )
    body = format_inline_comment_body(f)

    assert "[HIGH] Unchecked division by zero" in body
    assert "Ensure denominator is non-zero before dividing." in body
    assert "Proof by Execution" not in body


def test_format_inline_comment_with_proof() -> None:
    f = Finding(
        file="src/app.py",
        line=42,
        title="Crash on empty list",
        body="List index out of range.",
        severity=Severity.HIGH,
        is_reproduced=True,
        reproduction_code="def test_crash():\n    assert False",
        reproduction_output="Traceback (most recent call last):\n  AssertionError",
    )
    body = format_inline_comment_body(f)

    assert "Proof by Execution (Reproduced in Sandbox)" in body
    assert "def test_crash():" in body
    assert "AssertionError" in body


def test_format_top_level_summary_clean() -> None:
    review_output = ReviewOutput(
        summary="No defects found.",
        findings=[],
    )
    summary_md = format_top_level_summary(review_output, pr_title="Clean Refactor")

    assert "Clean Refactor" in summary_md
    assert "No critical defects or regressions detected" in summary_md
    assert "Governance Note:" in summary_md


def test_format_top_level_summary_with_findings() -> None:
    review_output = ReviewOutput(
        summary="Multiple critical defects identified.",
        findings=[
            Finding(
                file="src/db.py",
                line=12,
                title="SQL injection risk",
                body="Use parameterized queries.",
                severity=Severity.HIGH,
                is_reproduced=True,
            )
        ],
    )
    summary_md = format_top_level_summary(review_output, pr_title="DB Update")

    assert "Findings Summary:" in summary_md
    assert "| `src/db.py` | 12 | **HIGH** | SQL injection risk 🧪 |" in summary_md
    assert "Governance Note:" in summary_md


def test_review_publisher_publish_review() -> None:
    mock_client = MagicMock()
    mock_client.post_review.return_value = {"id": 1, "state": "COMMENTED"}

    publisher = ReviewPublisher(github_client=mock_client)
    review_output = ReviewOutput(
        summary="Found 1 defect.",
        findings=[
            Finding(
                file="src/auth.py",
                line=25,
                title="Token leakage",
                body="Header persists.",
                severity=Severity.HIGH,
            )
        ],
    )

    result = publisher.publish_review(
        owner="octocat",
        repo="hello-world",
        pull_number=10,
        head_sha="abcdef123456",
        review_output=review_output,
        pr_title="Fix auth",
    )

    assert result["state"] == "COMMENTED"
    mock_client.post_review.assert_called_once()
    _, kwargs = mock_client.post_review.call_args
    assert kwargs["owner"] == "octocat"
    assert kwargs["repo"] == "hello-world"
    assert kwargs["pull_number"] == 10
    assert kwargs["commit_id"] == "abcdef123456"
    assert len(kwargs["comments"]) == 1
    assert kwargs["comments"][0]["path"] == "src/auth.py"
    assert kwargs["comments"][0]["line"] == 25
    assert kwargs["comments"][0]["side"] == "RIGHT"
