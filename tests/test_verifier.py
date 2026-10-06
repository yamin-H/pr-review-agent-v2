"""Unit tests for FindingVerifier and adversarial verification filtering."""

import json
from unittest.mock import MagicMock

from review.agent.state import AgentState
from review.agent.tools.findings_tools import verify_finding
from review.cli import review_diff
from review.findings import Finding, Severity
from review.verifier import (
    FindingVerifier,
    VerificationDecision,
    VerificationResult,
)

SAMPLE_DIFF = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -10,3 +10,4 @@
 def handle_data(data):
     # Some logic
+    result = process(data)
+    return result
"""


def _mock_completion_choice(content: str) -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    response.usage.total_tokens = 42
    return response


def test_verifier_empty_findings() -> None:
    mock_client = MagicMock()
    mock_reviewer = MagicMock()
    mock_reviewer._ensure_client.return_value = mock_client

    verifier = FindingVerifier(reviewer=mock_reviewer)
    surviving, verdicts = verifier.verify_findings(
        findings=[],
        diff_text=SAMPLE_DIFF,
    )

    assert surviving == []
    assert verdicts == []
    mock_client.chat.completions.create.assert_not_called()


def test_verifier_keep_finding() -> None:
    f = Finding(
        file="app.py",
        line=12,
        title="Unchecked return value",
        body="Result might be None.",
        severity=Severity.HIGH,
    )

    mock_response_data = {
        "results": [
            {
                "finding_index": 0,
                "decision": "keep",
                "rationale": "Valid concern, process() can return None and caller will crash.",
            }
        ]
    }
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_completion_choice(
        json.dumps(mock_response_data)
    )

    mock_reviewer = MagicMock()
    mock_reviewer.model = "test-model"
    mock_reviewer._ensure_client.return_value = mock_client

    verifier = FindingVerifier(reviewer=mock_reviewer)
    surviving, verdicts = verifier.verify_findings(
        findings=[f],
        diff_text=SAMPLE_DIFF,
    )

    assert len(surviving) == 1
    assert surviving[0].title == "Unchecked return value"
    assert len(verdicts) == 1
    assert verdicts[0].decision == VerificationDecision.KEEP


def test_verifier_drop_finding() -> None:
    f = Finding(
        file="app.py",
        line=12,
        title="False positive warning",
        body="Harmless code wrongly flagged.",
        severity=Severity.LOW,
    )

    mock_response_data = {
        "results": [
            {
                "finding_index": 0,
                "decision": "drop",
                "rationale": "The process() call is already sanitized in handle_data callers.",
            }
        ]
    }
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_completion_choice(
        json.dumps(mock_response_data)
    )

    mock_reviewer = MagicMock()
    mock_reviewer.model = "test-model"
    mock_reviewer._ensure_client.return_value = mock_client

    verifier = FindingVerifier(reviewer=mock_reviewer)
    surviving, verdicts = verifier.verify_findings(
        findings=[f],
        diff_text=SAMPLE_DIFF,
    )

    assert len(surviving) == 0
    assert len(verdicts) == 1
    assert verdicts[0].decision == VerificationDecision.DROP


def test_verifier_revise_finding() -> None:
    f = Finding(
        file="app.py",
        line=12,
        title="Vague title",
        body="Vague body.",
        severity=Severity.LOW,
    )

    mock_response_data = {
        "results": [
            {
                "finding_index": 0,
                "decision": "revise",
                "rationale": "Issue is real but needs high severity and specific title.",
                "revised_title": "Uncaught Exception in process()",
                "revised_body": "process() can raise ValueError which is unhandled here.",
                "revised_severity": "high",
                "revised_line": 12,
            }
        ]
    }
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_completion_choice(
        json.dumps(mock_response_data)
    )

    mock_reviewer = MagicMock()
    mock_reviewer.model = "test-model"
    mock_reviewer._ensure_client.return_value = mock_client

    verifier = FindingVerifier(reviewer=mock_reviewer)
    surviving, verdicts = verifier.verify_findings(
        findings=[f],
        diff_text=SAMPLE_DIFF,
    )

    assert len(surviving) == 1
    assert surviving[0].title == "Uncaught Exception in process()"
    assert surviving[0].severity == Severity.HIGH
    assert surviving[0].line == 12


def test_verifier_revise_invalid_line_fallback() -> None:
    f = Finding(
        file="app.py",
        line=12,
        title="Real issue",
        body="Body",
        severity=Severity.MEDIUM,
    )

    # Line 999 is not in the diff, should fallback to original line 12
    mock_response_data = {
        "results": [
            {
                "finding_index": 0,
                "decision": "revise",
                "rationale": "Needs better body.",
                "revised_title": "Refined title",
                "revised_line": 999,
            }
        ]
    }
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_completion_choice(
        json.dumps(mock_response_data)
    )

    mock_reviewer = MagicMock()
    mock_reviewer.model = "test-model"
    mock_reviewer._ensure_client.return_value = mock_client

    verifier = FindingVerifier(reviewer=mock_reviewer)
    surviving, _ = verifier.verify_findings(
        findings=[f],
        diff_text=SAMPLE_DIFF,
    )

    assert len(surviving) == 1
    assert surviving[0].title == "Refined title"
    assert surviving[0].line == 12  # Retained original valid line


def test_verifier_self_healing_retry() -> None:
    f = Finding(
        file="app.py",
        line=12,
        title="Real defect",
        body="Body",
        severity=Severity.HIGH,
    )

    mock_client = MagicMock()
    # 1st attempt: invalid JSON
    # 2nd attempt: valid JSON
    bad_resp = _mock_completion_choice("not json")
    good_resp = _mock_completion_choice(
        json.dumps(
            {
                "results": [
                    {
                        "finding_index": 0,
                        "decision": "keep",
                        "rationale": "Valid.",
                    }
                ]
            }
        )
    )
    mock_client.chat.completions.create.side_effect = [bad_resp, good_resp]

    mock_reviewer = MagicMock()
    mock_reviewer.model = "test-model"
    mock_reviewer._ensure_client.return_value = mock_client

    verifier = FindingVerifier(reviewer=mock_reviewer)
    surviving, verdicts = verifier.verify_findings([f], SAMPLE_DIFF)

    assert len(surviving) == 1
    assert mock_client.chat.completions.create.call_count == 2


def test_verifier_fallback_on_error() -> None:
    f = Finding(
        file="app.py",
        line=12,
        title="Real defect",
        body="Body",
        severity=Severity.HIGH,
    )

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = RuntimeError("Network error")

    mock_reviewer = MagicMock()
    mock_reviewer.model = "test-model"
    mock_reviewer._ensure_client.return_value = mock_client

    verifier = FindingVerifier(reviewer=mock_reviewer)
    surviving, verdicts = verifier.verify_findings([f], SAMPLE_DIFF)

    # Fails safe by keeping the finding
    assert len(surviving) == 1
    assert surviving[0].title == "Real defect"


def test_verify_finding_tool() -> None:
    state = AgentState(
        diff=SAMPLE_DIFF,
        findings=[
            Finding(
                file="app.py",
                line=12,
                title="Candidate finding",
                body="Testing",
                severity=Severity.MEDIUM,
            )
        ],
    )

    mock_verifier = MagicMock()
    mock_verifier.verify_single.return_value = VerificationResult(
        finding_index=0,
        decision=VerificationDecision.DROP,
        rationale="Code is actually safe.",
    )

    # Drop test
    res = verify_finding(state=state, finding_index=1, verifier=mock_verifier)
    assert "DROPPED" in res
    assert len(state.findings) == 0

    # Empty state test
    res_empty = verify_finding(state=state, finding_index=1, verifier=mock_verifier)
    assert "empty" in res_empty.lower()


def test_review_diff_with_verify() -> None:
    mock_reviewer = MagicMock()
    # Mock review_chunk to emit 1 finding
    mock_review_output = MagicMock()
    mock_review_output.summary = "Initial summary"
    mock_review_output.findings = [
        Finding(
            file="app.py",
            line=12,
            title="Candidate bug",
            body="Bug body",
            severity=Severity.HIGH,
        )
    ]
    mock_reviewer.review_chunk.return_value = mock_review_output

    # Mock verifier to drop it
    mock_verifier = MagicMock()
    mock_verifier.verify_findings.return_value = ([], [])

    result = review_diff(
        diff_text=SAMPLE_DIFF,
        reviewer=mock_reviewer,
        verifier=mock_verifier,
        verify=True,
    )

    assert len(result.findings) == 0
    mock_verifier.verify_findings.assert_called_once()
