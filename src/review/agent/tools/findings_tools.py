"""Tools for proposing and validating inline review findings against diff ground truth."""

from review.agent.state import AgentState
from review.findings import Finding, Severity
from review.validate import validate_findings_against_diff
from review.verifier import FindingVerifier, VerificationDecision


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


def verify_finding(
    state: AgentState,
    finding_index: int,
    verifier: FindingVerifier | None = None,
) -> str:
    """Adversarially challenge and verify a candidate finding to filter false positives."""
    if not state.findings:
        return "No findings to verify. State findings list is empty."

    # Handle both 1-indexed (e.g. 1) and 0-indexed (e.g. 0) references
    target_idx = finding_index
    if 1 <= target_idx <= len(state.findings):
        target_idx -= 1
    elif not (0 <= target_idx < len(state.findings)):
        return (
            f"Error: Invalid finding index {finding_index}. "
            f"Valid range is 1 to {len(state.findings)} (or 0 to {len(state.findings) - 1})."
        )

    finding = state.findings[target_idx]
    if verifier is None:
        verifier = FindingVerifier()

    result = verifier.verify_single(
        finding=finding,
        diff_text=state.diff,
        pr_title=state.pr_title,
        pr_description=state.pr_description,
    )

    if result.decision == VerificationDecision.DROP:
        dropped = state.findings.pop(target_idx)
        return (
            f"DROPPED: Finding #{finding_index} '{dropped.title}' was disproven "
            f"as a false positive.\nRationale: {result.rationale}"
        )
    elif result.decision == VerificationDecision.REVISE:
        revised = Finding(
            file=finding.file,
            line=result.revised_line or finding.line,
            title=result.revised_title or finding.title,
            body=result.revised_body or finding.body,
            severity=result.revised_severity or finding.severity,
        )
        state.findings[target_idx] = revised
        return (
            f"REVISED: Finding #{finding_index} updated.\n"
            f"New Title: {revised.title}\n"
            f"New Severity: {revised.severity.value}\n"
            f"Rationale: {result.rationale}"
        )
    else:  # KEEP
        return (
            f"CONFIRMED: Finding #{finding_index} '{finding.title}' withstood "
            f"adversarial challenge.\nRationale: {result.rationale}"
        )
