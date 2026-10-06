"""Scripted Fake LLM for deterministic testing of the agent loop, budgets, and guardrails."""

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class ScriptedToolCall:
    """Represents a mock tool call emitted by the Fake LLM."""

    name: str
    arguments: dict[str, Any]
    thought: str = ""


class FakeLLM:
    """Deterministic scripted LLM that returns pre-configured tool calls for unit tests."""

    def __init__(
        self,
        scripted_steps: list[ScriptedToolCall] | None = None,
        default_tool: str = "submit_review",
        default_args: dict[str, Any] | None = None,
    ) -> None:
        self.scripted_steps = list(scripted_steps or [])
        self.default_tool = default_tool
        self.default_args = default_args or {"summary": "Automated fallback review summary."}
        self.invocations: list[list[dict[str, Any]]] = []

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Simulate an LLM completion turn.

        Returns (thought_text, list_of_tool_calls_with_json_args).
        """
        self.invocations.append(messages)

        if self.scripted_steps:
            step = self.scripted_steps.pop(0)
            tool_call = {
                "id": f"call_{len(self.invocations)}",
                "type": "function",
                "function": {
                    "name": step.name,
                    "arguments": json.dumps(step.arguments),
                },
            }
            return step.thought, [tool_call]

        # Default action when script is exhausted
        fallback_call = {
            "id": f"call_{len(self.invocations)}",
            "type": "function",
            "function": {
                "name": self.default_tool,
                "arguments": json.dumps(self.default_args),
            },
        }
        return "Concluded review.", [fallback_call]
