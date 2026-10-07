"""Unit tests for specialized subagent delegation and bounded execution."""

from pathlib import Path

from review.agent.state import AgentState
from review.agent.tools.subagent import delegate_subagent
from tests.fakes.fake_llm import FakeLLM, ScriptedToolCall

SAMPLE_DIFF = """\
diff --git a/auth.py b/auth.py
--- a/auth.py
+++ b/auth.py
@@ -10,3 +10,4 @@
 def check_access():
-    return False
+    # dangerous bypass
+    return True
"""


def test_subagent_recursion_prevention() -> None:
    child_state = AgentState(
        diff=SAMPLE_DIFF,
        is_subagent=True,
    )

    res = delegate_subagent(
        state=child_state,
        task_type="security_audit",
        instruction="Investigate bypass",
    )

    assert "REJECTED: Subagent cannot recursively delegate" in res


def test_subagent_execution_with_findings(tmp_path: Path) -> None:
    parent_state = AgentState(
        diff=SAMPLE_DIFF,
        repo="test-org/test-repo",
        pr_title="Authentication patch",
        is_subagent=False,
    )

    fake_llm = FakeLLM(
        scripted_steps=[
            ScriptedToolCall(
                name="add_finding",
                arguments={
                    "file": "auth.py",
                    "line": 12,
                    "title": "Auth Bypass Vulnerability",
                    "body": "Always returns True bypassing auth check",
                    "severity": "critical",
                },
                thought="Detected hardcoded True in check_access.",
            ),
            ScriptedToolCall(
                name="submit_review",
                arguments={
                    "summary": "Security audit complete: Critical bypass identified in auth.py.",
                },
                thought="Submitting security findings.",
            ),
        ]
    )

    result = delegate_subagent(
        state=parent_state,
        task_type="security_audit",
        instruction="Check for auth bypasses in auth.py",
        focus_files=["auth.py"],
        repo_root=tmp_path,
        llm_client=fake_llm,
    )

    assert "Subagent Report: SECURITY_AUDIT" in result
    assert "New Findings Added: 1" in result
    assert len(parent_state.findings) == 1
    assert "[SECURITY_AUDIT]" in parent_state.findings[0].title
    assert "Critical bypass" in parent_state.subagent_reports[0]["summary"]


def test_subagent_clean_run_no_findings(tmp_path: Path) -> None:
    parent_state = AgentState(
        diff=SAMPLE_DIFF,
        is_subagent=False,
    )

    fake_llm = FakeLLM(
        scripted_steps=[
            ScriptedToolCall(
                name="submit_review",
                arguments={"summary": "No vulnerabilities found in module."},
                thought="Review finished cleanly.",
            )
        ]
    )

    result = delegate_subagent(
        state=parent_state,
        task_type="impact_investigation",
        instruction="Assess caller impact",
        repo_root=tmp_path,
        llm_client=fake_llm,
    )

    assert "Subagent Report: IMPACT_INVESTIGATION" in result
    assert "New Findings Added: 0" in result
    assert len(parent_state.findings) == 0
    assert len(parent_state.subagent_reports) == 1
