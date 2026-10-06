import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from review.agent.state import AgentState

logger = logging.getLogger("review.budgets")


class BudgetConfig(BaseModel):
    """Enforceable budget thresholds that govern the agent runtime."""

    max_steps: int = Field(default=15, ge=1, description="Maximum tool execution steps allowed")
    max_tokens: int = Field(default=100_000, ge=1, description="Maximum cumulative tokens budget")
    max_cost_usd: float = Field(
        default=0.50, ge=0.0, description="Maximum cumulative inference cost in USD"
    )
    loop_threshold: int = Field(
        default=2,
        ge=2,
        description="Number of consecutive identical tool calls that constitutes a loop",
    )


class LoopDetector:
    """Tracks tool invocation fingerprints to intercept runaway repetition loops."""

    def __init__(self, threshold: int = 2) -> None:
        self.threshold = threshold
        self._history: list[str] = []

    def _make_fingerprint(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        try:
            serialized_input = json.dumps(tool_input, sort_keys=True)
        except (TypeError, ValueError):
            serialized_input = str(sorted(tool_input.items()))
        return f"{tool_name}:{serialized_input}"

    def record_and_check(self, tool_name: str, tool_input: dict[str, Any]) -> bool:
        """Record a tool invocation and return True if a repetition loop is detected."""
        fingerprint = self._make_fingerprint(tool_name, tool_input)
        self._history.append(fingerprint)

        if len(self._history) < self.threshold:
            return False

        # Check if the last N invocations are identical
        last_n = self._history[-self.threshold :]
        return all(fp == fingerprint for fp in last_n)


class BudgetEnforcer:
    """Non-negotiable program guardrails enforcing step limits and loop interception."""

    def __init__(self, config: BudgetConfig | None = None) -> None:
        self.config = config or BudgetConfig()
        self.loop_detector = LoopDetector(threshold=self.config.loop_threshold)

    def check_and_update(
        self,
        state: AgentState,
        tool_name: str,
        tool_input: dict[str, Any],
        tokens: int = 0,
        cost: float = 0.0,
    ) -> tuple[bool, str | None]:
        """Check budget constraints before or after taking a step.

        Returns (should_halt, halt_reason). If halted, updates state.is_finished to True.
        """
        state.total_tokens_used += tokens
        state.estimated_cost_usd += cost

        # 1. Step Budget Check
        if state.step_count >= self.config.max_steps:
            reason = (
                f"Forced termination: step budget exceeded "
                f"({state.step_count}/{self.config.max_steps} steps)."
            )
            logger.warning(reason)
            state.is_finished = True
            state.final_summary = (state.final_summary + f"\n\n[Budget Guard] {reason}").strip()
            return True, reason

        # 2. Token Budget Check
        if state.total_tokens_used > self.config.max_tokens:
            reason = (
                f"Forced termination: token budget exceeded "
                f"({state.total_tokens_used}/{self.config.max_tokens} tokens)."
            )
            logger.warning(reason)
            state.is_finished = True
            state.final_summary = (state.final_summary + f"\n\n[Budget Guard] {reason}").strip()
            return True, reason

        # 3. Loop Interception Check
        if self.loop_detector.record_and_check(tool_name, tool_input):
            reason = (
                f"Forced termination: execution loop detected for tool '{tool_name}' "
                f"repeated {self.config.loop_threshold} times with identical arguments."
            )
            logger.warning(reason)
            state.is_finished = True
            state.final_summary = (state.final_summary + f"\n\n[Budget Guard] {reason}").strip()
            return True, reason

        return False, None
