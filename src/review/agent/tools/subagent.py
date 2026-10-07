"""Specialized subagent delegation tool for bounded, focused investigations."""

import logging
from pathlib import Path
from typing import Any

from review.agent.budgets import BudgetConfig
from review.agent.state import AgentState
from review.findings import Finding

logger = logging.getLogger("review.agent.tools.subagent")

SUPPORTED_SUBAGENT_ROLES = {
    "security_audit": (
        "Security Audit Specialist: Focus exclusively on detecting injection vulnerabilities, "
        "authentication/authorization flaws, credential leakage, untrusted deserialization, "
        "and insecure subprocess invocations."
    ),
    "impact_investigation": (
        "Impact & Blast Radius Analyst: Focus exclusively on identifying caller dependencies, "
        "breaking API changes, signature mismatches, and affected downstream modules."
    ),
    "test_coverage": (
        "Test Coverage & Edge Case Auditor: Focus exclusively on identifying untested corner "
        "cases, missing unit tests, null/empty edge conditions, and exception propagation."
    ),
    "general": "General Review Assistant: Perform a focused investigation on specific files.",
}


def delegate_subagent(
    state: AgentState,
    task_type: str,
    instruction: str,
    focus_files: list[str] | None = None,
    repo_root: Path | None = None,
    reviewer: Any | None = None,
    llm_client: Any | None = None,
) -> str:
    """Spawn a focused, bounded subagent to perform a specialized investigation.

    Args:
        state: Parent AgentState.
        task_type: One of 'security_audit', 'impact_investigation', 'test_coverage', 'general'.
        instruction: Specific directive detailing what the subagent must investigate.
        focus_files: Optional subset of files to prioritize.
        repo_root: Optional root directory of the repository.
        reviewer: Optional GroqReviewer instance.
        llm_client: Optional mock LLM client (e.g. for testing).

    Returns:
        Consolidated textual report of findings and analysis from the subagent.
    """
    if state.is_subagent:
        return "REJECTED: Subagent cannot recursively delegate to further subagents."

    clean_task_type = task_type.lower().strip()
    role_description = SUPPORTED_SUBAGENT_ROLES.get(
        clean_task_type, SUPPORTED_SUBAGENT_ROLES["general"]
    )

    effective_repo_root = repo_root or Path.cwd()
    files_clause = (
        f"\nPrioritized Target Files: {', '.join(focus_files)}"
        if focus_files
        else ""
    )

    child_prompt = (
        f"=== SPECIALIZED SUBAGENT TASK: {clean_task_type.upper()} ===\n"
        f"Role: {role_description}\n"
        f"Instruction: {instruction}\n"
        f"{files_clause}\n\n"
        "Investigate thoroughly using your tools. Use `read_file`, `analyze_impact`, "
        "`run_static_analysis`, or `get_blame`. If you find real defects, call `add_finding`. "
        "When finished, call `submit_review` with your summarized conclusions."
    )

    # Prepare isolated child state with strict sub-budget
    child_state = AgentState(
        diff=state.diff,
        repo=state.repo,
        pr_title=state.pr_title,
        pr_description=state.pr_description,
        is_subagent=True,
    )

    # Avoid circular import at module load
    from review.agent.graph import AgentRunner

    child_runner = AgentRunner(
        reviewer=reviewer,
        llm_client=llm_client,
        budget_config=BudgetConfig(
            max_steps=5,
            max_tokens=15000,
            max_cost_usd=0.05,
        ),
    )

    logger.info("Spawning subagent '%s' for instruction: %s", clean_task_type, instruction[:80])

    try:
        # Run child agent loop
        child_state = child_runner.run(
            diff_text=state.diff,
            repo_root=effective_repo_root,
            repo=state.repo,
            pr_title=state.pr_title,
            pr_description=state.pr_description,
            existing_state=child_state,
            initial_prompt=child_prompt,
        )
    except Exception as e:
        logger.error("Subagent execution failed: %s", e)
        return f"Subagent '{clean_task_type}' failed with error: {e}"

    # Extract child results
    child_summary = child_state.final_summary or "Subagent completed investigation."
    child_findings = child_state.findings

    # Absorb findings into parent state (annotating their origin)
    absorbed_count = 0
    for finding in child_findings:
        tagged_finding = Finding(
            file=finding.file,
            line=finding.line,
            title=f"[{clean_task_type.upper()}] {finding.title}",
            body=f"{finding.body}\n\n*(Reported by {clean_task_type} subagent)*",
            severity=finding.severity,
            citation=finding.citation,
        )
        state.findings.append(tagged_finding)
        absorbed_count += 1

    # Record trace in parent state
    report = {
        "task_type": clean_task_type,
        "instruction": instruction,
        "steps_taken": child_state.step_count,
        "findings_discovered": len(child_findings),
        "summary": child_summary,
    }
    state.subagent_reports.append(report)

    # Format return message for parent agent
    return (
        f"=== Subagent Report: {clean_task_type.upper()} ===\n"
        f"Steps Executed: {child_state.step_count}\n"
        f"New Findings Added: {absorbed_count}\n"
        f"Subagent Summary:\n{child_summary}"
    )


__all__ = ["SUPPORTED_SUBAGENT_ROLES", "delegate_subagent"]
