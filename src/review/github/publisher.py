"""Publisher formatting and submitting structured reviews and inline comments to GitHub."""

import logging
from typing import Any

from review.findings import Finding, ReviewOutput
from review.github.client import GitHubClient

logger = logging.getLogger("review.github.publisher")


def format_inline_comment_body(finding: Finding) -> str:
    """Format a single finding into GitHub Flavored Markdown for an inline review comment."""
    severity_icons = {
        "high": "🚨",
        "medium": "⚠️",
        "low": "🔍",
        "info": "ℹ️",
    }
    icon = severity_icons.get(finding.severity.value, "🔍")
    title_line = f"### {icon} [{finding.severity.value.upper()}] {finding.title}"

    parts = [title_line, finding.body]

    if finding.citation:
        parts.append(f"> 📚 **Repository Precedent Cited:** *{finding.citation}*")

    if finding.is_reproduced:
        parts.append(
            "\n<details>\n<summary>🧪 <b>Proof by Execution (Reproduced in Sandbox)</b></summary>\n"
        )
        if finding.reproduction_code:
            parts.append(f"```python\n{finding.reproduction_code}\n```")
        if finding.reproduction_output:
            parts.append(f"\n**Execution Trace:**\n```\n{finding.reproduction_output}\n```")
        parts.append("</details>")

    return "\n\n".join(parts)


def format_top_level_summary(
    review_output: ReviewOutput,
    pr_title: str = "",
) -> str:
    """Format the overarching review comment body submitted to the pull request."""
    lines: list[str] = [
        "## 🤖 Autonomous PR Review Report",
        f"**Pull Request:** {pr_title or 'Untitled'}\n",
        f"{review_output.summary}\n",
    ]

    total_findings = len(review_output.findings)
    reproduced_count = sum(1 for f in review_output.findings if f.is_reproduced)

    if total_findings == 0:
        lines.append("✅ **All changes inspected. No critical defects or regressions detected.**")
    else:
        lines.append(
            f"**Findings Summary:** {total_findings} item(s) flagged "
            f"({reproduced_count} experimentally reproduced in sandbox).\n"
        )
        lines.append("| File | Line | Severity | Issue |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for f in review_output.findings:
            repro_badge = " 🧪" if f.is_reproduced else ""
            sev = f.severity.value.upper()
            lines.append(f"| `{f.file}` | {f.line} | **{sev}** | {f.title}{repro_badge} |")

    lines.append(
        "\n---\n> **Governance Note:** This review was autonomously generated. "
        "Reviewers provide diagnostic feedback only and never approve or block pull requests."
    )

    return "\n".join(lines)


class ReviewPublisher:
    """Submits structured review comments and inline annotations to GitHub."""

    def __init__(self, github_client: GitHubClient) -> None:
        self.client = github_client

    def publish_review(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        head_sha: str,
        review_output: ReviewOutput,
        pr_title: str = "",
    ) -> dict[str, Any]:
        """Publish the overall review and inline line comments to the GitHub PR."""
        summary_body = format_top_level_summary(review_output, pr_title=pr_title)

        inline_comments: list[dict[str, Any]] = []
        for finding in review_output.findings:
            comment_payload = {
                "path": finding.file,
                "line": finding.line,
                "side": "RIGHT",
                "body": format_inline_comment_body(finding),
            }
            inline_comments.append(comment_payload)

        logger.info(
            "Publishing review to %s/%s#%d with %d inline comments at %s",
            owner,
            repo,
            pull_number,
            len(inline_comments),
            head_sha[:7],
        )

        return self.client.post_review(
            owner=owner,
            repo=repo,
            pull_number=pull_number,
            commit_id=head_sha,
            body=summary_body,
            comments=inline_comments,
            event="COMMENT",
        )
