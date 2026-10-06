"""Read-only repository inspection tools with directory sandbox protections."""

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
