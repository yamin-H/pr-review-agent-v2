"""Implementation of the SZZ algorithm to mine real bug-introducing commits.

SZZ traces bug-fixing commits back to the exact historical commit that
introduced the defective code using git blame analysis.
"""

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

# Standard SZZ heuristic regex for bug-fixing commits
BUG_FIX_PATTERN = re.compile(
    r"\b(fix(es|ed)?|close[sd]?|resolve[sd]?)\b.*?(#\d+|gh-\d+|bug|issue|defect)",
    re.IGNORECASE,
)
HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def run_git(args: list[str], cwd: Path) -> str:
    """Execute a git command in the target directory and return stdout."""
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def find_bug_fixing_commits(repo_path: Path, max_commits: int = 50) -> list[tuple[str, str]]:
    """Scan git log for commits whose commit message indicates a bug fix."""
    log_output = run_git(["log", f"-n{max_commits}", "--pretty=format:%H%x09%s"], cwd=repo_path)
    fixes: list[tuple[str, str]] = []

    for line in log_output.splitlines():
        if "\t" not in line:
            continue
        sha, subject = line.split("\t", 1)
        if BUG_FIX_PATTERN.search(subject):
            fixes.append((sha, subject))

    return fixes


def extract_deleted_lines_from_fix(repo_path: Path, bfc_sha: str) -> list[tuple[str, int]]:
    """Extract (file_path, old_line_number) for lines removed or modified in a bug fix."""
    diff_text = run_git(["diff", f"{bfc_sha}^", bfc_sha], cwd=repo_path)
    deleted_lines: list[tuple[str, int]] = []
    current_file: str | None = None
    old_line = 0

    for line in diff_text.splitlines():
        if line.startswith("--- a/"):
            current_file = line[6:].strip()
            if current_file == "/dev/null":
                current_file = None
        elif match := HUNK_HEADER.match(line):
            old_line = int(match.group(1))
        elif line.startswith("-") and not line.startswith("---"):
            if current_file is not None:
                deleted_lines.append((current_file, old_line))
            old_line += 1
        elif line.startswith(" "):
            old_line += 1

    return deleted_lines


def trace_bug_introducing_commits(
    repo_path: Path,
    bfc_sha: str,
    deleted_lines: list[tuple[str, int]],
) -> dict[str, list[tuple[str, int]]]:
    """Blame the parent of the bug fix to identify which commits introduced the deleted lines."""
    bic_to_lines: dict[str, list[tuple[str, int]]] = {}

    for file_path, line_no in deleted_lines:
        try:
            blame_out = run_git(
                ["blame", "-w", "-C", f"-L{line_no},{line_no}", f"{bfc_sha}^", "--", file_path],
                cwd=repo_path,
            )
            # First token on the line is the commit SHA
            bic_sha = blame_out.split()[0].lstrip("^")
            if len(bic_sha) >= 7:
                bic_to_lines.setdefault(bic_sha, []).append((file_path, line_no))
        except subprocess.CalledProcessError:
            continue

    return bic_to_lines


def build_szz_dataset(
    repo_path: Path,
    output_dir: Path,
    limit: int = 10,
) -> int:
    """Mine bug-fixing and bug-introducing commits using SZZ and save as eval cases."""
    output_dir.mkdir(parents=True, exist_ok=True)
    repo_name = repo_path.resolve().name
    fixes = find_bug_fixing_commits(repo_path)
    print(f"Found {len(fixes)} candidate bug-fixing commits in {repo_name}")

    count = 0
    for bfc_sha, bfc_msg in fixes:
        deleted = extract_deleted_lines_from_fix(repo_path, bfc_sha)
        if not deleted:
            continue

        bic_map = trace_bug_introducing_commits(repo_path, bfc_sha, deleted)

        for bic_sha, line_info in bic_map.items():
            try:
                bic_diff = run_git(["show", "--format=", bic_sha], cwd=repo_path)
                bic_subject = run_git(["log", "-1", "--pretty=format:%s", bic_sha], cwd=repo_path)
            except subprocess.CalledProcessError:
                continue

            ground_truth: list[dict[str, Any]] = []
            for file_path, _ in line_info:
                ground_truth.append(
                    {
                        "file": file_path,
                        "line": line_info[0][1],
                        "description": (
                            f"Defect introduced here, later fixed in {bfc_sha[:8]}: {bfc_msg}"
                        ),
                        "severity": "high",
                    }
                )

            case_data = {
                "id": f"szz-{repo_name}-{bic_sha[:8]}",
                "repo": repo_name,
                "commit_sha": bic_sha,
                "title": bic_subject,
                "diff": bic_diff,
                "fix_commit_sha": bfc_sha,
                "fix_commit_message": bfc_msg,
                "ground_truth": ground_truth,
                "is_clean": False,
            }

            out_file = output_dir / f"{repo_name}_{bic_sha[:8]}.json"
            out_file.write_text(json.dumps(case_data, indent=2), encoding="utf-8")
            count += 1
            print(f"Recorded SZZ case: Bug introduced in {bic_sha[:8]} -> Fixed in {bfc_sha[:8]}")

            if count >= limit:
                break
        if count >= limit:
            break

    print(f"\nBuilt {count} SZZ dataset cases in {output_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Mine SZZ bug-introducing commits from a Git repo")
    parser.add_argument(
        "--repo-path", required=True, type=Path, help="Path to local cloned repository"
    )
    parser.add_argument("--limit", type=int, default=10, help="Maximum SZZ cases to extract")
    parser.add_argument(
        "--output-dir",
        default="evals/dataset/szz",
        help="Directory to save extracted SZZ cases",
    )
    args = parser.parse_args()

    return build_szz_dataset(
        repo_path=args.repo_path,
        output_dir=Path(args.output_dir),
        limit=args.limit,
    )


if __name__ == "__main__":
    import sys

    sys.exit(main())
