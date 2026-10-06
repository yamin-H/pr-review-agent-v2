from review.findings import Finding, Severity
from review.validate import validate_finding_lines, validate_findings_against_diff

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


def test_validate_finding_lines_valid() -> None:
    findings = [
        Finding(
            file="app.py",
            line=13,
            title="Bug",
            body="Off by one",
            severity=Severity.HIGH,
        )
    ]
    valid_lines = {("app.py", 13)}

    valid, errors = validate_finding_lines(findings, valid_lines)
    assert len(valid) == 1
    assert len(errors) == 0


def test_validate_findings_against_diff_filters_invalid_lines() -> None:
    findings = [
        Finding(
            file="app.py",
            line=13,
            title="Valid Finding",
            body="Line 13 is added in diff.",
            severity=Severity.MEDIUM,
        ),
        Finding(
            file="app.py",
            line=999,
            title="Hallucinated Finding",
            body="Line 999 is not in diff.",
            severity=Severity.HIGH,
        ),
        Finding(
            file="other.py",
            line=13,
            title="Wrong File Finding",
            body="File is not in diff.",
            severity=Severity.LOW,
        ),
    ]

    valid, errors = validate_findings_against_diff(findings, SAMPLE_DIFF)
    assert len(valid) == 1
    assert valid[0].line == 13
    assert len(errors) == 2
    assert any("999" in err for err in errors)
    assert any("other.py" in err for err in errors)
