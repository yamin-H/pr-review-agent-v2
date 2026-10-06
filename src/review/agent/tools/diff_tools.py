"""Read-only diff tools for inspecting pull request changes with grounded line numbers."""

from review.chunker import parse_diff_hunks
from review.security import wrap_untrusted


def list_changed_files(diff_text: str) -> list[str]:
    """Return the list of reviewable changed files in the pull request diff."""
    file_diffs = parse_diff_hunks(diff_text)
    return [path for path, _ in file_diffs]


def get_diff(diff_text: str, file_path: str) -> str:
    """Return the formatted diff for a specific file, with real new-file line numbers prefixed.

    All diff content is securely wrapped in untrusted data delimiters.
    """
    file_diffs = parse_diff_hunks(diff_text)
    for path, hunks in file_diffs:
        if path == file_path:
            formatted_hunks = [h.format_numbered() for h in hunks]
            content = f"File: {path}\n\n" + "\n\n".join(formatted_hunks)
            return wrap_untrusted(content, label="diff")

    return f"Error: File '{file_path}' was not found in the reviewable diff."
