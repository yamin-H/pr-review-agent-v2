"""Data models for review execution telemetry, spans, and traces."""

import time
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SpanType(StrEnum):
    """Categorization of execution spans within a review lifecycle."""

    LLM_REASONING = "llm_reasoning"
    TOOL_EXECUTION = "tool_execution"
    VERIFIER = "verifier"
    SANDBOX = "sandbox"
    MEMORY = "memory"
    TOTAL = "total"


class Span(BaseModel):
    """A single timed execution block within a review trace."""

    name: str = Field(..., description="Descriptive identifier of the span operation")
    span_type: SpanType = Field(..., description="Stage category of the span")
    start_time: float = Field(
        default_factory=time.time, description="Start timestamp (epoch seconds)"
    )
    end_time: float | None = Field(default=None, description="End timestamp (epoch seconds)")
    duration_ms: float = Field(default=0.0, description="Duration in milliseconds")
    attributes: dict[str, Any] = Field(
        default_factory=dict, description="Metadata key-value pairs"
    )
    error: str | None = Field(default=None, description="Exception or error message if failed")

    def finish(self, error: str | None = None) -> None:
        """Close span and calculate elapsed duration."""
        if self.end_time is None:
            self.end_time = time.time()
            self.duration_ms = max(0.0, (self.end_time - self.start_time) * 1000.0)
        if error:
            self.error = error


class ReviewTrace(BaseModel):
    """Full execution trace of a pull request review session."""

    trace_id: str = Field(..., description="Unique trace identifier")
    repo: str = Field(default="", description="Repository identifier (owner/repo)")
    pull_number: int | None = Field(default=None, description="Pull request number")
    delivery_id: str | None = Field(default=None, description="GitHub delivery UUID")
    start_time: float = Field(default_factory=time.time, description="Start timestamp")
    end_time: float | None = Field(default=None, description="Completion timestamp")
    total_duration_ms: float = Field(default=0.0, description="Total execution duration in ms")
    status: str = Field(
        default="success", description="Status: success, failed, budget_halt, empty_diff"
    )

    prompt_tokens: int = Field(default=0, description="Total input tokens consumed")
    completion_tokens: int = Field(default=0, description="Total output tokens generated")
    total_tokens: int = Field(default=0, description="Cumulative tokens used")
    estimated_cost_usd: float = Field(default=0.0, description="Total inference cost in USD")

    findings_count: int = Field(default=0, description="Total candidate findings generated")
    findings_verified: int = Field(default=0, description="Findings evaluated by verifier")
    findings_rejected: int = Field(default=0, description="False positives discarded by verifier")
    reproductions_attempted: int = Field(
        default=0, description="Reproduction tests attempted in sandbox"
    )
    reproductions_succeeded: int = Field(
        default=0, description="Defects proven via sandbox reproduction"
    )
    precedents_cited: int = Field(default=0, description="Repository precedents cited")

    spans: list[Span] = Field(default_factory=list, description="Ordered list of executed spans")

    def finish(self, status: str = "success") -> None:
        """Finalize the review trace."""
        self.status = status
        if self.end_time is None:
            self.end_time = time.time()
            self.total_duration_ms = max(0.0, (self.end_time - self.start_time) * 1000.0)

    def get_latency_breakdown(self) -> dict[str, float]:
        """Aggregate total duration in milliseconds per span type."""
        breakdown: dict[str, float] = {st.value: 0.0 for st in SpanType if st != SpanType.TOTAL}
        for span in self.spans:
            if span.span_type.value in breakdown:
                breakdown[span.span_type.value] += span.duration_ms
        return breakdown
