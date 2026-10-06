from pathlib import Path

from review.agent.budgets import BudgetConfig
from review.agent.graph import AgentRunner
from tests.fakes.fake_llm import FakeLLM, ScriptedToolCall

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


def test_agent_runner_happy_path(tmp_path: Path) -> None:
    # Scripted sequence of steps
    scripted_steps = [
        ScriptedToolCall("list_changed_files", {}, thought="First let me list the files."),
        ScriptedToolCall("get_diff", {"file_path": "app.py"}, thought="Let me check diff."),
        ScriptedToolCall(
            "add_finding",
            {
                "file": "app.py",
                "line": 13,
                "title": "Unchecked addition",
                "body": "Check this added line",
                "severity": "high",
            },
            thought="Found an issue on line 13.",
        ),
        ScriptedToolCall(
            "submit_review",
            {"summary": "Changes look good with 1 bug found."},
            thought="Submitting review.",
        ),
    ]

    fake_llm = FakeLLM(scripted_steps=scripted_steps)
    runner = AgentRunner(llm_client=fake_llm)

    state = runner.run(diff_text=SAMPLE_DIFF, repo_root=tmp_path, pr_title="Add new line")

    assert state.is_finished is True
    assert state.step_count == 4
    assert len(state.findings) == 1
    assert state.findings[0].line == 13
    assert state.final_summary == "Changes look good with 1 bug found."


def test_agent_runner_halts_on_loop(tmp_path: Path) -> None:
    # Repeating identical calls
    scripted_steps = [
        ScriptedToolCall("read_file", {"file_path": "app.py"}),
        ScriptedToolCall("read_file", {"file_path": "app.py"}),
    ]

    fake_llm = FakeLLM(scripted_steps=scripted_steps)
    config = BudgetConfig(loop_threshold=2)
    runner = AgentRunner(llm_client=fake_llm, budget_config=config)

    state = runner.run(diff_text=SAMPLE_DIFF, repo_root=tmp_path)

    assert state.is_finished is True
    assert "execution loop detected" in state.final_summary


def test_agent_runner_halts_on_max_steps(tmp_path: Path) -> None:
    # More calls than allowed
    scripted_steps = [
        ScriptedToolCall("list_changed_files", {}),
        ScriptedToolCall("list_changed_files", {}),
    ]

    fake_llm = FakeLLM(scripted_steps=scripted_steps)
    config = BudgetConfig(max_steps=1, loop_threshold=5)
    runner = AgentRunner(llm_client=fake_llm, budget_config=config)

    state = runner.run(diff_text=SAMPLE_DIFF, repo_root=tmp_path)

    assert state.is_finished is True
    assert "step budget exceeded" in state.final_summary


def test_agent_runner_review_to_output(tmp_path: Path) -> None:
    scripted_steps = [
        ScriptedToolCall("submit_review", {"summary": "Direct finish."}),
    ]

    fake_llm = FakeLLM(scripted_steps=scripted_steps)
    runner = AgentRunner(llm_client=fake_llm)

    output = runner.review_to_output(diff_text=SAMPLE_DIFF, repo_root=tmp_path)
    assert output.summary == "Direct finish."
    assert output.findings == []
