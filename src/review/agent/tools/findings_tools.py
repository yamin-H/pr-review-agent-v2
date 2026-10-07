"""Tools for proposing, validating, reproducing, and searching repository precedents."""

from pathlib import Path

from review.agent.state import AgentState
from review.agent.tools.memory_tools import search_precedents
from review.findings import Finding, Severity
from review.sandbox.prover import ProofEngine
from review.validate import validate_findings_against_diff
from review.verifier import FindingVerifier, VerificationDecision


def add_finding(
    state: AgentState,
    file: str,
    line: int,
    title: str,
    body: str,
    severity: str = "medium",
    citation: str | None = None,
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
        citation=citation,
    )

    valid_findings, errors = validate_findings_against_diff([candidate], state.diff)
    if not valid_findings:
        err_msg = errors[0] if errors else "Line is not an added line in the diff."
        return f"REJECTED: {err_msg} You must cite an exact new-file line number from the diff."

    state.findings.append(candidate)
    citation_note = f" (Cited: {citation})" if citation else ""
    return (
        f"ACCEPTED: Finding #{len(state.findings)} added for '{file}:{line}' "
        f"[{sev.value.upper()}] - {title}{citation_note}"
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
            citation=finding.citation,
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


def run_reproduction_test(
    state: AgentState,
    finding_index: int,
    repo_root: Path | None = None,
    test_code: str | None = None,
    proof_engine: ProofEngine | None = None,
) -> str:
    """Attempt to reproduce a proposed finding in the execution sandbox."""
    if not state.findings:
        return "No findings to reproduce. State findings list is empty."

    target_idx = finding_index
    if 1 <= target_idx <= len(state.findings):
        target_idx -= 1
    elif not (0 <= target_idx < len(state.findings)):
        return (
            f"Error: Invalid finding index {finding_index}. "
            f"Valid range is 1 to {len(state.findings)} (or 0 to {len(state.findings) - 1})."
        )

    finding = state.findings[target_idx]
    if proof_engine is None:
        proof_engine = ProofEngine()

    updated_finding, result = proof_engine.attempt_reproduction(
        finding=finding,
        diff_text=state.diff,
        repo_root=repo_root,
        custom_test_code=test_code,
    )
    state.findings[target_idx] = updated_finding

    if result.reproduced:
        return (
            f"REPRODUCED: Finding #{finding_index} '{finding.title}' was successfully "
            f"reproduced in the sandbox!\n"
            f"Exit Code: {result.exit_code}\n"
            f"Duration: {result.duration_ms:.0f}ms\n"
            f"Failure Summary: {result.error_summary or 'Assertion/Exception triggered'}"
        )
    elif result.timed_out:
        return (
            f"TIMEOUT: Sandbox execution timed out while attempting to reproduce "
            f"Finding #{finding_index}."
        )
    else:
        return (
            f"NOT REPRODUCED: Finding #{finding_index} did not trigger an expected failure "
            f"in the sandbox (exit code {result.exit_code}).\n"
            f"Output: {result.error_summary or result.stdout or 'No failure observed'}"
        )


__all__ = [
    "add_finding",
    "run_reproduction_test",
    "search_precedents",
    "verify_finding",
]
