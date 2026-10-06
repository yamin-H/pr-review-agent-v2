from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Finding(BaseModel):
    file: str = Field(..., description="Target file path relative to repo root")
    line: int = Field(..., ge=1, description="New-file line number in the diff")
    title: str = Field(..., description="Brief one-line summary of the finding")
    body: str = Field(..., description="Detailed explanation of the issue and recommendation")
    severity: Severity = Field(default=Severity.MEDIUM, description="Finding severity level")


class ReviewOutput(BaseModel):
    summary: str = Field(..., description="Overall summary comment for the PR")
    findings: list[Finding] = Field(default_factory=list, description="List of inline findings")


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
