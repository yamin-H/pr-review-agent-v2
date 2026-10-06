# Backward compatibility aliases for findings and validation
from review.findings import Finding, ReviewOutput, Severity
from review.validate import validate_finding_lines, validate_findings_against_diff

__all__ = [
    "Finding",
    "ReviewOutput",
    "Severity",
    "validate_finding_lines",
    "validate_findings_against_diff",
]
