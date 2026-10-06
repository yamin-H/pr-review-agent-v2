"""Tool for finalizing and concluding the autonomous review run."""

from review.agent.state import AgentState


def submit_review(state: AgentState, summary: str) -> str:
    """Finalize the review with an overall summary comment and mark the agent run as finished."""
    state.final_summary = summary
    state.is_finished = True
    return (
        f"SUCCESS: Review finalized with summary ({len(summary)} chars) and "
        f"{len(state.findings)} inline finding(s). The run is now complete."
    )
