"""Script to collect real merged PRs and human review comments from GitHub.

Connects to GitHub's REST API, downloads the unified diff and review comments,
and formats them into ground-truth evaluation cases.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()


def fetch_pr_diff(client: httpx.Client, owner: str, repo: str, pr_number: int) -> str:
    """Fetch the raw unified diff for a pull request."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    headers = {"Accept": "application/vnd.github.v3.diff"}
    resp = client.get(url, headers=headers)
    resp.raise_for_status()
    return resp.text


def fetch_pr_comments(
    client: httpx.Client, owner: str, repo: str, pr_number: int
) -> list[dict[str, Any]]:
    """Fetch inline review comments for a pull request."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/comments"
    resp = client.get(url)
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


def collect_prs(
    repo_name: str,
    output_dir: Path,
    limit: int = 10,
    github_token: str | None = None,
) -> int:
    """Collect closed/merged PRs with review comments from GitHub and save to output_dir."""
    token = github_token or os.getenv("GITHUB_TOKEN")
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    owner, repo = repo_name.split("/", 1)
    output_dir.mkdir(parents=True, exist_ok=True)

    with httpx.Client(headers=headers, timeout=30.0) as client:
        # Fetch closed PRs
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
        params: dict[str, str | int] = {
            "state": "closed",
            "per_page": min(limit, 30),
            "sort": "updated",
            "direction": "desc",
        }
        resp = client.get(url, params=params)
        if resp.status_code == 403 and "rate limit exceeded" in resp.text.lower():
            print(
                "Error: GitHub API rate limit exceeded. Set GITHUB_TOKEN to increase limits.",
                file=sys.stderr,
            )
            return 1
        resp.raise_for_status()
        pulls = resp.json()

        saved_count = 0
        for pr in pulls:
            if not pr.get("merged_at"):
                continue  # Only evaluate merged PRs

            pr_number = pr["number"]
            diff_text = fetch_pr_diff(client, owner, repo, pr_number)
            raw_comments = fetch_pr_comments(client, owner, repo, pr_number)

            ground_truth = []
            for comment in raw_comments:
                path = comment.get("path")
                line = comment.get("line") or comment.get("original_line")
                body = comment.get("body", "").strip()
                if path and line:
                    ground_truth.append(
                        {
                            "file": path,
                            "line": line,
                            "description": body,
                            "severity": "medium",
                        }
                    )

            is_clean = len(ground_truth) == 0

            case_data = {
                "id": f"{owner}-{repo}-pr-{pr_number}",
                "repo": f"{owner}/{repo}",
                "pr_number": pr_number,
                "title": pr.get("title", ""),
                "url": pr.get("html_url", ""),
                "diff": diff_text,
                "ground_truth": ground_truth,
                "is_clean": is_clean,
            }

            out_file = output_dir / f"{owner}_{repo}_{pr_number}.json"
            out_file.write_text(json.dumps(case_data, indent=2), encoding="utf-8")
            saved_count += 1
            print(
                f"Saved PR #{pr_number} ({len(ground_truth)} comments, "
                f"clean={is_clean}) -> {out_file.name}"
            )

            if saved_count >= limit:
                break

    print(f"\nSuccessfully collected {saved_count} PR cases in {output_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect real PRs from GitHub for evaluation")
    parser.add_argument(
        "--repo", required=True, help="GitHub repo in 'owner/repo' format (e.g. pallets/flask)"
    )
    parser.add_argument("--limit", type=int, default=5, help="Maximum PRs to collect")
    parser.add_argument(
        "--output-dir",
        default="evals/dataset/prs",
        help="Directory where collected PR cases will be stored",
    )
    args = parser.parse_args()

    return collect_prs(
        repo_name=args.repo,
        output_dir=Path(args.output_dir),
        limit=args.limit,
    )


if __name__ == "__main__":
    sys.exit(main())
