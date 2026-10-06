import pytest
from pydantic import ValidationError

from review.models import Finding, ReviewOutput, Severity, validate_finding_lines


def test_finding_valid_creation() -> None:
    finding = Finding(
        file="app.py",
        line=12,
        title="Unchecked None",
        body="Value can be None if key is absent.",
        severity=Severity.HIGH,
    )
    assert finding.file == "app.py"
    assert finding.line == 12
    assert finding.severity == Severity.HIGH


def test_finding_rejects_non_positive_line() -> None:
    with pytest.raises(ValidationError):
        Finding(
            file="app.py",
            line=0,
            title="Invalid Line",
            body="Line numbers must be >= 1.",
            severity=Severity.LOW,
        )


def test_review_output_serialization() -> None:
    output = ReviewOutput(
        summary="Looks mostly good with one potential bug.",
        findings=[
            Finding(
                file="src/main.py",
                line=42,
                title="Off-by-one error",
                body="Loop bounds should be exclusive.",
                severity=Severity.MEDIUM,
            )
        ],
    )
    data = output.model_dump()
    assert data["summary"] == "Looks mostly good with one potential bug."
    assert len(data["findings"]) == 1
    assert data["findings"][0]["severity"] == "medium"


def test_validate_finding_lines_accepts_valid_lines() -> None:
    valid_set = {("src/main.py", 42), ("src/main.py", 43)}
    findings = [
        Finding(
            file="src/main.py",
            line=42,
            title="Valid Finding",
            body="Valid line number.",
            severity=Severity.LOW,
        )
    ]

    valid, errors = validate_finding_lines(findings, valid_set)
    assert len(valid) == 1
    assert len(errors) == 0


def test_validate_finding_lines_rejects_hallucinated_lines() -> None:
    valid_set = {("src/main.py", 42)}
    findings = [
        Finding(
            file="src/main.py",
            line=999,
            title="Hallucinated Finding",
            body="Not in diff.",
            severity=Severity.HIGH,
        )
    ]

    valid, errors = validate_finding_lines(findings, valid_set)
    assert len(valid) == 0
    assert len(errors) == 1
    assert "Line 999 in 'src/main.py' is not an added line in the diff" in errors[0]
