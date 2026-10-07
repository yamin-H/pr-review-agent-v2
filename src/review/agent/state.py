from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from review.findings import Finding


class AgentStep(BaseModel):
    """Record of a single step/action taken by the review agent."""

    step_number: int = Field(..., description="1-indexed sequence number of the step")
    tool_name: str = Field(..., description="Name of the tool executed")
    tool_input: dict[str, Any] = Field(
        default_factory=dict, description="Parameters passed to the tool"
    )
    tool_output_summary: str = Field(default="", description="Sanitized summary of tool output")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentState(BaseModel):
    """Comprehensive execution state of the autonomous review agent."""

    diff: str = Field(..., description="Original pull request unified diff")
    repo: str = Field(default="", description="Target repository name (owner/repo)")
    pr_title: str = Field(default="", description="Title of the PR under review")
    pr_description: str = Field(default="", description="Description of the PR")

    review_plan: list[str] = Field(
        default_factory=list,
        description="Dynamic review plan listing prioritized files and risks",
    )
    findings: list[Finding] = Field(
        default_factory=list,
        description="Candidate findings discovered during review",
    )
    steps: list[AgentStep] = Field(
        default_factory=list,
        description="Chronological audit trace of actions taken",
    )

    is_finished: bool = Field(
        default=False, description="Flag indicating if the review is complete"
    )
    final_summary: str = Field(default="", description="Consolidated review summary comment")

    total_tokens_used: int = Field(default=0, description="Cumulative tokens consumed")
    estimated_cost_usd: float = Field(default=0.0, description="Estimated inference cost in USD")

    @property
    def step_count(self) -> int:
        """Total number of tool execution steps completed."""
        return len(self.steps)

    def record_action(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        output_summary: str,
    ) -> AgentStep:
        """Append an executed tool action to the state history."""
        step = AgentStep(
            step_number=self.step_count + 1,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output_summary=output_summary,
        )
        self.steps.append(step)
        return step
