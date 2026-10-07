from enum import StrEnum

from pydantic import BaseModel, Field


class Severity(StrEnum):
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
    reproduction_code: str | None = Field(
        default=None, description="Minimal standalone Python reproduction test script"
    )
    reproduction_output: str | None = Field(
        default=None, description="Execution stdout/traceback proving the defect"
    )
    is_reproduced: bool | None = Field(
        default=None, description="Whether the defect was confirmed via execution"
    )
    citation: str | None = Field(
        default=None, description="Precedent reference, issue, or PR citation"
    )


class ReviewOutput(BaseModel):
    summary: str = Field(..., description="Overall summary comment for the PR")
    findings: list[Finding] = Field(default_factory=list, description="List of inline findings")
