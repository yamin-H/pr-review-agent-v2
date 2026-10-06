"""Unit and integration tests for sandbox execution and reproduction proof engine."""

from pathlib import Path
from unittest.mock import MagicMock

from review.agent.state import AgentState
from review.agent.tools.findings_tools import run_reproduction_test
from review.cli import review_diff
from review.findings import Finding, Severity
from review.sandbox.models import ExecutionResult
from review.sandbox.prover import ProofEngine
from review.sandbox.runner import SandboxRunner
from review.sandbox.synthesizer import ReproductionSynthesizer, extract_python_code

SAMPLE_DIFF = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -10,3 +10,4 @@
 def parse_json(data):
+    return json.loads(data)
"""


def test_extract_python_code_fenced() -> None:
    raw = "Here is the code:\n```python\ndef test_bug():\n    assert False\n```\nDone."
    code = extract_python_code(raw)
    assert code == "def test_bug():\n    assert False"


def test_extract_python_code_generic() -> None:
    raw = "```\nx = 1\n```"
    code = extract_python_code(raw)
    assert code == "x = 1"


def test_sandbox_runner_safe_env_strips_keys(monkeypatch: object) -> None:
    runner = SandboxRunner()
    safe_env = runner._build_safe_env({"TEST_VAR": "123"})
    assert "GROQ_API_KEY" not in safe_env
    assert "GITHUB_TOKEN" not in safe_env
    assert safe_env.get("TEST_VAR") == "123"
    assert safe_env.get("PYTHONDONTWRITEBYTECODE") == "1"


def test_sandbox_runner_successful_execution() -> None:
    runner = SandboxRunner()
    script = "print('sandbox execution successful')"
    result = runner.run_script(script)

    assert result.exit_code == 0
    assert "sandbox execution successful" in result.stdout
    assert not result.timed_out
    assert not result.reproduced


def test_sandbox_runner_reproduced_assertion_failure() -> None:
    runner = SandboxRunner()
    script = "raise AssertionError('Critical flaw detected')"
    result = runner.run_script(script)

    assert result.exit_code != 0
    assert result.reproduced is True
    assert "AssertionError" in result.stderr
    assert result.error_summary is not None
    assert "AssertionError" in result.error_summary


def test_sandbox_runner_timeout() -> None:
    runner = SandboxRunner(timeout_seconds=0.2)
    script = "import time\ntime.sleep(2.0)\n"
    result = runner.run_script(script)

    assert result.timed_out is True
    assert result.exit_code == -1
    assert result.reproduced is False
    assert "timed out" in (result.error_summary or "")


def test_synthesizer_mock_call() -> None:
    f = Finding(
        file="app.py",
        line=11,
        title="Uncaught JSONDecodeError",
        body="json.loads raises decode error on invalid json",
        severity=Severity.HIGH,
    )

    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "```python\nimport json\njson.loads('bad')\n```"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_client.chat.completions.create.return_value = mock_response

    mock_reviewer = MagicMock()
    mock_reviewer.model = "test-model"
    mock_reviewer._ensure_client.return_value = mock_client

    synth = ReproductionSynthesizer(reviewer=mock_reviewer)
    code = synth.synthesize_test(f, SAMPLE_DIFF)

    assert "json.loads('bad')" in code


def test_proof_engine_attempt_reproduction_success() -> None:
    f = Finding(
        file="app.py",
        line=11,
        title="ZeroDivisionError",
        body="Division by zero crash",
        severity=Severity.HIGH,
    )

    mock_synth = MagicMock()
    mock_synth.synthesize_test.return_value = "1 / 0"

    runner = SandboxRunner()
    engine = ProofEngine(synthesizer=mock_synth, runner=runner)

    updated_f, result = engine.attempt_reproduction(f, SAMPLE_DIFF)

    assert updated_f.is_reproduced is True
    assert updated_f.reproduction_code == "1 / 0"
    assert updated_f.reproduction_output is not None
    assert "ZeroDivisionError" in updated_f.reproduction_output
    assert result.reproduced is True


def test_proof_engine_attempt_reproduction_clean_run() -> None:
    f = Finding(
        file="app.py",
        line=11,
        title="False alarm",
        body="Harmless",
        severity=Severity.LOW,
    )

    mock_synth = MagicMock()
    mock_synth.synthesize_test.return_value = "x = 1 + 1"

    runner = SandboxRunner()
    engine = ProofEngine(synthesizer=mock_synth, runner=runner)

    updated_f, result = engine.attempt_reproduction(f, SAMPLE_DIFF)

    assert updated_f.is_reproduced is False
    assert result.reproduced is False


def test_run_reproduction_test_tool() -> None:
    state = AgentState(
        diff=SAMPLE_DIFF,
        findings=[
            Finding(
                file="app.py",
                line=11,
                title="Bug to reproduce",
                body="Body",
                severity=Severity.HIGH,
            )
        ],
    )

    mock_engine = MagicMock()
    mock_res = ExecutionResult(
        exit_code=1,
        reproduced=True,
        duration_ms=45.0,
        error_summary="AssertionError: reproduced successfully",
    )
    mock_engine.attempt_reproduction.return_value = (state.findings[0], mock_res)

    res = run_reproduction_test(
        state=state,
        finding_index=1,
        repo_root=Path("."),
        proof_engine=mock_engine,
    )

    assert "REPRODUCED" in res
    assert "AssertionError" in res


def test_review_diff_with_reproduce() -> None:
    mock_reviewer = MagicMock()
    mock_out = MagicMock()
    mock_out.summary = "Diff review"
    mock_out.findings = [
        Finding(
            file="app.py",
            line=11,
            title="Real crash",
            body="Body",
            severity=Severity.HIGH,
        )
    ]
    mock_reviewer.review_chunk.return_value = mock_out

    mock_engine = MagicMock()
    mock_proven_finding = Finding(
        file="app.py",
        line=11,
        title="Real crash",
        body="Body",
        severity=Severity.HIGH,
        is_reproduced=True,
        reproduction_output="Traceback: crash",
    )
    mock_engine.prove_findings.return_value = [mock_proven_finding]

    res = review_diff(
        diff_text=SAMPLE_DIFF,
        reviewer=mock_reviewer,
        proof_engine=mock_engine,
        reproduce=True,
    )

    assert len(res.findings) == 1
    assert res.findings[0].is_reproduced is True
    mock_engine.prove_findings.assert_called_once()
