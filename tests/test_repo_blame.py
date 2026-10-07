"""Unit tests for git blame repository tool."""

from pathlib import Path

from review.agent.tools.repo_tools import get_blame


def test_get_blame_real_git_file() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    result = get_blame(repo_root, "pyproject.toml", start_line=1, end_line=5)

    assert "Git Blame: pyproject.toml" in result
    assert "[L   1]" in result
    assert "Yamin Hossain" in result or "sha:" in result.lower() or "author" in result.lower()


def test_get_blame_invalid_line_range() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    res_neg = get_blame(repo_root, "pyproject.toml", start_line=-1, end_line=5)
    assert "Error: Invalid line range" in res_neg

    res_inv = get_blame(repo_root, "pyproject.toml", start_line=10, end_line=2)
    assert "Error: Invalid line range" in res_inv


def test_get_blame_nonexistent_file(tmp_path: Path) -> None:
    res = get_blame(tmp_path, "missing.py", start_line=1, end_line=5)
    assert "Error: File not found" in res


def test_get_blame_path_traversal(tmp_path: Path) -> None:
    res = get_blame(tmp_path, "../../etc/passwd", start_line=1, end_line=5)
    assert "Error: Access denied or invalid path" in res
