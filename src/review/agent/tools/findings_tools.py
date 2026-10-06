"""Tools for proposing and validating inline review findings against diff ground truth."""

from review.agent.state import AgentState
from review.findings import Finding, Severity
from review.validate import validate_findings_against_diff


def add_finding(
    state: AgentState,
    file: str,
    line: int,
    title: str,
    body: str,
    severity: str = "medium",
) -> str:
    """Validate a candidate finding against the diff and record it if legitimate.

    Rejects findings whose line number does not exist within the added diff lines.
    """
    try:
        sev = Severity(severity.lower())
    except ValueError:
        sev = Severity.MEDIUM

    candidate = Finding(
        file=file,
        line=line,
        title=title,
        body=body,
        severity=sev,
    )

    valid_findings, errors = validate_findings_against_diff([candidate], state.diff)
    if not valid_findings:
        err_msg = errors[0] if errors else "Line is not an added line in the diff."
        return f"REJECTED: {err_msg} You must cite an exact new-file line number from the diff."

    state.findings.append(candidate)
    return (
        f"ACCEPTED: Finding #{len(state.findings)} added for '{file}:{line}' "
        f"[{sev.value.upper()}] - {title}"
    )
