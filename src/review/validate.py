from review.diff import added_lines
from review.findings import Finding


def validate_finding_lines(
    findings: list[Finding],
    valid_lines: set[tuple[str, int]],
) -> tuple[list[Finding], list[str]]:
    """Validate findings against allowed (filename, line_number) pairs from the diff.

    Returns a tuple of (valid_findings, error_messages).
    Any finding whose file and line do not exist in valid_lines is rejected.
    """
    valid: list[Finding] = []
    errors: list[str] = []

    for finding in findings:
        if (finding.file, finding.line) in valid_lines:
            valid.append(finding)
        else:
            errors.append(
                f"Line {finding.line} in '{finding.file}' is not an added line in the diff."
            )

    return valid, errors


def validate_findings_against_diff(
    findings: list[Finding],
    diff_text: str,
) -> tuple[list[Finding], list[str]]:
    """Extract valid lines from raw diff text and validate candidate findings."""
    parsed_lines = added_lines(diff_text)
    valid_set = {(item.path, item.line) for item in parsed_lines}
    return validate_finding_lines(findings, valid_set)
