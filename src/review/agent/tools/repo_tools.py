import subprocess
from datetime import UTC, datetime
from pathlib import Path

from review.filters import should_review
from review.security import wrap_untrusted

IGNORED_SEARCH_DIRS = {".git", ".venv", "node_modules", "__pycache__", "dist", "build"}


def safe_resolve_path(repo_root: Path, target_path: str) -> Path | None:
    """Resolve a relative file path safely within the repo_root boundary.

    Prevents directory traversal attacks (e.g. '../../etc/passwd').
    """
    try:
        resolved_root = repo_root.resolve()
        candidate = (resolved_root / target_path).resolve()
        if candidate.is_relative_to(resolved_root):
            return candidate
    except Exception:
        return None
    return None


def read_file(
    repo_root: Path,
    file_path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    max_lines: int = 200,
) -> str:
    """Read contents of a file within the repository, with optional line range bounding.

    Prefixes lines with their 1-indexed file line numbers and wraps the content safely.
    """
    target = safe_resolve_path(repo_root, file_path)
    if target is None:
        return f"Error: Access denied or invalid path: '{file_path}'"

    if not target.exists() or not target.is_file():
        return f"Error: File not found: '{file_path}'"

    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        return f"Error reading '{file_path}': {e}"

    total = len(lines)
    s_line = max(1, start_line) if start_line is not None else 1
    e_line = min(total, end_line) if end_line is not None else total

    if s_line > total:
        return f"Error: start_line {s_line} exceeds total file length of {total} lines."

    if e_line - s_line + 1 > max_lines:
        e_line = s_line + max_lines - 1

    formatted_lines: list[str] = []
    for line_num in range(s_line, e_line + 1):
        formatted_lines.append(f"{line_num:4d} | {lines[line_num - 1]}")

    body = f"File: {file_path} (Lines {s_line}-{e_line} of {total})\n\n" + "\n".join(
        formatted_lines
    )
    return wrap_untrusted(body, label="file_content")


def search_code(
    repo_root: Path,
    query: str,
    max_matches: int = 15,
) -> str:
    """Search for a string or keyword across repository files within allowed directories."""
    resolved_root = repo_root.resolve()
    matches: list[str] = []

    for path in resolved_root.rglob("*"):
        if not path.is_file():
            continue

        rel_parts = path.relative_to(resolved_root).parts
        if any(ignored in rel_parts for ignored in IGNORED_SEARCH_DIRS):
            continue

        rel_str = str(path.relative_to(resolved_root)).replace("\\", "/")
        if not should_review(rel_str):
            continue

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        if query in content:
            for idx, line in enumerate(content.splitlines(), 1):
                if query in line:
                    matches.append(f"{rel_str}:{idx}: {line.strip()[:100]}")
                    if len(matches) >= max_matches:
                        break
        if len(matches) >= max_matches:
            break

    if not matches:
        return f"No occurrences of '{query}' found in repository."

    result_text = f"Search Results for '{query}' ({len(matches)} matches):\n" + "\n".join(matches)
    return wrap_untrusted(result_text, label="search_results")


def get_blame(
    repo_root: Path,
    file_path: str,
    start_line: int,
    end_line: int,
) -> str:
    """Retrieve git blame history for a specific line range in a file.

    Args:
        repo_root: Path to the repository root containing .git directory.
        file_path: Relative path to the file.
        start_line: 1-indexed starting line number.
        end_line: 1-indexed ending line number.

    Returns:
        Formatted git blame records with commit SHAs, authors, dates, and line contents.
    """
    target = safe_resolve_path(repo_root, file_path)
    if target is None:
        return f"Error: Access denied or invalid path: '{file_path}'"

    if not target.exists() or not target.is_file():
        return f"Error: File not found: '{file_path}'"

    if start_line < 1 or end_line < start_line:
        return f"Error: Invalid line range: {start_line}-{end_line}"

    # Bounded range to avoid excessive memory usage
    max_range = 100
    if end_line - start_line + 1 > max_range:
        end_line = start_line + max_range - 1

    try:
        cmd = [
            "git",
            "blame",
            "-L",
            f"{start_line},{end_line}",
            "--porcelain",
            file_path,
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(repo_root.resolve()),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"Error: git blame timed out for '{file_path}'"
    except Exception as e:
        return f"Error executing git blame for '{file_path}': {e}"

    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip()
        return f"git blame failed for '{file_path}': {err}"

    # Parse porcelain output
    blame_lines: list[str] = []
    current_commit: str = ""
    current_author: str = ""
    current_date: str = ""
    current_summary: str = ""
    commit_data: dict[str, tuple[str, str, str]] = {}  # sha -> (author, date, summary)

    raw_lines = proc.stdout.splitlines()
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i]
        if not line:
            i += 1
            continue

        parts = line.split()
        if len(parts) >= 4 and len(parts[0]) == 40:
            current_commit = parts[0][:8]
            final_lineno = parts[2]
            # Read metadata lines following the commit header
            i += 1
            while i < len(raw_lines) and not raw_lines[i].startswith("\t"):
                meta_line = raw_lines[i]
                if meta_line.startswith("author "):
                    current_author = meta_line[7:].strip()
                elif meta_line.startswith("author-time "):
                    try:
                        ts = int(meta_line[12:].strip())
                        current_date = datetime.fromtimestamp(ts, tz=UTC).strftime(
                            "%Y-%m-%d %H:%M"
                        )
                    except Exception:
                        current_date = "unknown"
                elif meta_line.startswith("summary "):
                    current_summary = meta_line[8:].strip()
                i += 1

            if current_commit in commit_data:
                current_author, current_date, current_summary = commit_data[current_commit]
            else:
                commit_data[current_commit] = (current_author, current_date, current_summary)

            code_line = ""
            if i < len(raw_lines) and raw_lines[i].startswith("\t"):
                code_line = raw_lines[i][1:]
                i += 1

            blame_lines.append(
                f"[L{final_lineno:>4}] {current_commit} ({current_author:<16} {current_date}) "
                f"| {code_line}"
            )
        else:
            i += 1

    if not blame_lines:
        return f"No git blame records found for '{file_path}' (lines {start_line}-{end_line})."

    output_str = (
        f"Git Blame: {file_path} (Lines {start_line}-{end_line})\n"
        + "\n".join(blame_lines)
    )
    return wrap_untrusted(output_str, label="git_blame")
