"""Execution telemetry tracker for managing spans, latencies, and token accounting."""

import logging
import time
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from review.telemetry.cost import calculate_cost_usd
from review.telemetry.models import ReviewTrace, Span, SpanType

logger = logging.getLogger("review.telemetry.tracker")


class TelemetryTracker:
    """Manages active execution spans, token usage, and cost calculation for a review session."""

    def __init__(
        self,
        repo: str = "",
        pull_number: int | None = None,
        delivery_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        self.trace = ReviewTrace(
            trace_id=trace_id or f"trace_{uuid.uuid4().hex[:12]}",
            repo=repo,
            pull_number=pull_number,
            delivery_id=delivery_id,
            start_time=time.time(),
        )

    @contextmanager
    def span(
        self,
        name: str,
        span_type: SpanType,
        attributes: dict[str, Any] | None = None,
    ) -> Generator[Span, None, None]:
        """Context manager to measure and record execution time of a code block."""
        s = Span(
            name=name,
            span_type=span_type,
            start_time=time.time(),
            attributes=attributes or {},
        )
        try:
            yield s
        except Exception as exc:
            s.finish(error=str(exc))
            self.trace.spans.append(s)
            raise
        else:
            s.finish()
            self.trace.spans.append(s)

    def record_tokens(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        model: str,
    ) -> None:
        """Accumulate token counts and calculate additional dollar cost."""
        self.trace.prompt_tokens += prompt_tokens
        self.trace.completion_tokens += completion_tokens
        self.trace.total_tokens += prompt_tokens + completion_tokens

        added_cost = calculate_cost_usd(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        self.trace.estimated_cost_usd = round(self.trace.estimated_cost_usd + added_cost, 6)

    def record_finding_verification(self, passed: bool) -> None:
        """Record adversarial verifier evaluation result."""
        self.trace.findings_verified += 1
        if not passed:
            self.trace.findings_rejected += 1

    def record_reproduction(self, succeeded: bool) -> None:
        """Record sandbox reproduction test outcome."""
        self.trace.reproductions_attempted += 1
        if succeeded:
            self.trace.reproductions_succeeded += 1

    def record_precedent_citation(self) -> None:
        """Record a repository precedent citation."""
        self.trace.precedents_cited += 1

    def finish(
        self,
        status: str = "success",
        findings_count: int | None = None,
    ) -> ReviewTrace:
        """Finalize the review trace and calculate total runtime."""
        if findings_count is not None:
            self.trace.findings_count = findings_count
        self.trace.finish(status=status)
        return self.trace
