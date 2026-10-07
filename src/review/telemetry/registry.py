"""Thread-safe telemetry metrics registry and Prometheus exporter."""

import threading
from collections import deque
from typing import Any

from review.telemetry.models import ReviewTrace


class MetricsRegistry:
    """Thread-safe registry aggregating operational KPIs and buffering recent review traces."""

    def __init__(self, max_recent_traces: int = 100) -> None:
        self._lock = threading.RLock()
        self._max_recent_traces = max_recent_traces

        # Review counters
        self._reviews_total = 0
        self._reviews_by_status: dict[str, int] = {
            "success": 0,
            "failed": 0,
            "budget_halt": 0,
            "empty_diff": 0,
        }

        # Finding metrics
        self._findings_total = 0
        self._findings_by_severity: dict[str, int] = {
            "high": 0,
            "medium": 0,
            "low": 0,
            "info": 0,
        }
        self._findings_verified = 0
        self._findings_rejected = 0

        # Sandbox & Memory metrics
        self._reproductions_attempted = 0
        self._reproductions_succeeded = 0
        self._precedents_cited = 0

        # Tokens & Cost
        self._tokens_total = 0
        self._prompt_tokens_total = 0
        self._completion_tokens_total = 0
        self._cost_usd_total = 0.0

        # Latencies in milliseconds
        self._durations_ms: list[float] = []

        # Ring buffer for recent traces
        self._recent_traces: deque[ReviewTrace] = deque(maxlen=max_recent_traces)

    def record_trace(self, trace: ReviewTrace) -> None:
        """Record a completed review trace and update aggregate statistics."""
        with self._lock:
            self._reviews_total += 1
            status_key = trace.status if trace.status in self._reviews_by_status else "failed"
            self._reviews_by_status[status_key] += 1

            self._findings_total += trace.findings_count
            self._findings_verified += trace.findings_verified
            self._findings_rejected += trace.findings_rejected
            self._reproductions_attempted += trace.reproductions_attempted
            self._reproductions_succeeded += trace.reproductions_succeeded
            self._precedents_cited += trace.precedents_cited

            self._tokens_total += trace.total_tokens
            self._prompt_tokens_total += trace.prompt_tokens
            self._completion_tokens_total += trace.completion_tokens
            self._cost_usd_total = round(self._cost_usd_total + trace.estimated_cost_usd, 6)

            if trace.total_duration_ms > 0:
                self._durations_ms.append(trace.total_duration_ms)

            self._recent_traces.append(trace)

    def record_finding_severity(self, severity: str) -> None:
        """Increment count for a specific severity."""
        with self._lock:
            sev_key = severity.lower()
            if sev_key in self._findings_by_severity:
                self._findings_by_severity[sev_key] += 1

    def get_summary(self) -> dict[str, Any]:
        """Return an operational summary of review agent metrics."""
        with self._lock:
            total_verified_ops = self._findings_verified + self._findings_rejected
            fp_rate = (
                round(self._findings_rejected / total_verified_ops, 4)
                if total_verified_ops > 0
                else 0.0
            )

            repro_rate = (
                round(
                    self._reproductions_succeeded / self._reproductions_attempted,
                    4,
                )
                if self._reproductions_attempted > 0
                else 0.0
            )

            precedent_rate = (
                round(self._precedents_cited / self._findings_total, 4)
                if self._findings_total > 0
                else 0.0
            )

            durations = sorted(self._durations_ms)
            avg_duration = round(sum(durations) / len(durations), 2) if durations else 0.0
            p50_duration = (
                round(durations[int(len(durations) * 0.50)], 2) if durations else 0.0
            )
            p95_duration = (
                round(durations[int(len(durations) * 0.95)], 2) if durations else 0.0
            )

            return {
                "reviews": {
                    "total": self._reviews_total,
                    "by_status": dict(self._reviews_by_status),
                },
                "findings": {
                    "total": self._findings_total,
                    "by_severity": dict(self._findings_by_severity),
                    "verified": self._findings_verified,
                    "rejected_false_positives": self._findings_rejected,
                    "false_positive_filter_rate": fp_rate,
                },
                "reproduction": {
                    "attempted": self._reproductions_attempted,
                    "succeeded": self._reproductions_succeeded,
                    "success_rate": repro_rate,
                },
                "memory": {
                    "precedents_cited": self._precedents_cited,
                    "precedent_citation_rate": precedent_rate,
                },
                "resources": {
                    "total_tokens": self._tokens_total,
                    "prompt_tokens": self._prompt_tokens_total,
                    "completion_tokens": self._completion_tokens_total,
                    "total_cost_usd": self._cost_usd_total,
                },
                "latency_ms": {
                    "avg": avg_duration,
                    "p50": p50_duration,
                    "p95": p95_duration,
                },
            }

    def get_recent_traces(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recent review traces serialized to dictionaries."""
        with self._lock:
            traces = list(self._recent_traces)[-limit:]
            traces.reverse()
            return [t.model_dump() for t in traces]

    def get_prometheus_metrics(self) -> str:
        """Export metrics formatted according to the Prometheus / OpenMetrics standard."""
        with self._lock:
            summary = self.get_summary()
            lines: list[str] = [
                "# HELP pr_reviews_total Total number of pull request reviews processed",
                "# TYPE pr_reviews_total counter",
            ]
            for status, count in self._reviews_by_status.items():
                lines.append(f'pr_reviews_total{{status="{status}"}} {count}')

            lines.extend(
                [
                    "# HELP pr_findings_total Total findings identified by severity",
                    "# TYPE pr_findings_total counter",
                ]
            )
            for sev, count in self._findings_by_severity.items():
                lines.append(f'pr_findings_total{{severity="{sev}"}} {count}')

            lines.extend(
                [
                    "# HELP pr_verifier_rejected_total Total false-positive findings filtered",
                    "# TYPE pr_verifier_rejected_total counter",
                    f"pr_verifier_rejected_total {self._findings_rejected}",
                    "# HELP pr_verifier_verified_total Total findings approved through verifier",
                    "# TYPE pr_verifier_verified_total counter",
                    f"pr_verifier_verified_total {self._findings_verified}",
                    "# HELP pr_sandbox_reproductions_total Sandbox reproductions by outcome",
                    "# TYPE pr_sandbox_reproductions_total counter",
                    (
                        f'pr_sandbox_reproductions_total{{status="succeeded"}} '
                        f"{self._reproductions_succeeded}"
                    ),
                    (
                        f'pr_sandbox_reproductions_total{{status="attempted"}} '
                        f"{self._reproductions_attempted}"
                    ),
                    "# HELP pr_precedents_cited_total Total repository memory precedents cited",
                    "# TYPE pr_precedents_cited_total counter",
                    f"pr_precedents_cited_total {self._precedents_cited}",
                    "# HELP pr_tokens_consumed_total Cumulative LLM tokens consumed",
                    "# TYPE pr_tokens_consumed_total counter",
                    f'pr_tokens_consumed_total{{type="prompt"}} {self._prompt_tokens_total}',
                    (
                        f'pr_tokens_consumed_total{{type="completion"}} '
                        f"{self._completion_tokens_total}"
                    ),
                    f'pr_tokens_consumed_total{{type="total"}} {self._tokens_total}',
                    "# HELP pr_cost_usd_total Cumulative inference cost in USD",
                    "# TYPE pr_cost_usd_total counter",
                    f"pr_cost_usd_total {self._cost_usd_total:.6f}",
                    "# HELP pr_review_duration_ms_avg Average review duration in milliseconds",
                    "# TYPE pr_review_duration_ms_avg gauge",
                    f"pr_review_duration_ms_avg {summary['latency_ms']['avg']:.2f}",
                ]
            )
            return "\n".join(lines) + "\n"

    def clear(self) -> None:
        """Reset all metrics (primarily for test isolation)."""
        with self._lock:
            self._reviews_total = 0
            for k in self._reviews_by_status:
                self._reviews_by_status[k] = 0
            self._findings_total = 0
            for k in self._findings_by_severity:
                self._findings_by_severity[k] = 0
            self._findings_verified = 0
            self._findings_rejected = 0
            self._reproductions_attempted = 0
            self._reproductions_succeeded = 0
            self._precedents_cited = 0
            self._tokens_total = 0
            self._prompt_tokens_total = 0
            self._completion_tokens_total = 0
            self._cost_usd_total = 0.0
            self._durations_ms.clear()
            self._recent_traces.clear()


# Global singleton registry instance
_GLOBAL_REGISTRY = MetricsRegistry()


def get_global_registry() -> MetricsRegistry:
    """Retrieve the global metrics registry singleton."""
    return _GLOBAL_REGISTRY
