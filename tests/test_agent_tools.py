from pathlib import Path

from review.agent.state import AgentState
from review.agent.tools import (
    add_finding,
    execute_tool,
    get_diff,
    list_changed_files,
    read_file,
    search_code,
    submit_review,
)

SAMPLE_DIFF = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -10,3 +12,4 @@
 context line
-old line
+new line
+another new line
 context line
"""


def test_diff_tools() -> None:
    files = list_changed_files(SAMPLE_DIFF)
    assert files == ["app.py"]

    diff_content = get_diff(SAMPLE_DIFF, "app.py")
    assert "File: app.py" in diff_content
    assert "  13 | +new line" in diff_content

    missing = get_diff(SAMPLE_DIFF, "nonexistent.py")
    assert "Error: File 'nonexistent.py' was not found" in missing


def test_repo_tools_read_file(tmp_path: Path) -> None:
    test_file = tmp_path / "hello.py"
    test_file.write_text("line 1\nline 2\nline 3\nline 4\n", encoding="utf-8")

    out = read_file(tmp_path, "hello.py", start_line=2, end_line=3)
    assert "   2 | line 2" in out
    assert "   3 | line 3" in out
    assert "line 1" not in out

    # Path traversal protection
    traversal = read_file(tmp_path, "../outside.py")
    assert "Error: Access denied" in traversal


def test_repo_tools_search_code(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "code.py").write_text("def find_me(): pass\n", encoding="utf-8")

    result = search_code(tmp_path, "find_me")
    assert "sub/code.py:1: def find_me(): pass" in result

    not_found = search_code(tmp_path, "missing_symbol_xyz")
    assert "No occurrences" in not_found


def test_findings_tools_add_finding() -> None:
    state = AgentState(diff=SAMPLE_DIFF)

    # Valid line 13
    res_valid = add_finding(
        state=state,
        file="app.py",
        line=13,
        title="Check bounds",
        body="Bound check needed",
        severity="high",
    )
    assert "ACCEPTED" in res_valid
    assert len(state.findings) == 1

    # Invalid line 999 (not in diff) -> must be rejected
    res_invalid = add_finding(
        state=state,
        file="app.py",
        line=999,
        title="Hallucination",
        body="Fake",
        severity="low",
    )
    assert "REJECTED" in res_invalid
    assert len(state.findings) == 1  # count remains 1


def test_submit_review_tool() -> None:
    state = AgentState(diff=SAMPLE_DIFF)
    assert state.is_finished is False

    res = submit_review(state, "Looks mostly good.")
    assert "SUCCESS" in res
    assert state.is_finished is True
    assert state.final_summary == "Looks mostly good."


def test_execute_tool_dispatcher(tmp_path: Path) -> None:
    state = AgentState(diff=SAMPLE_DIFF)
    out = execute_tool("list_changed_files", {}, state, tmp_path)
    assert "app.py" in out

    unknown = execute_tool("fake_tool", {}, state, tmp_path)
    assert "Error: Unknown tool" in unknown
