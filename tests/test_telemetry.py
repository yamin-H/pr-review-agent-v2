"""Unit and integration tests for operational telemetry, metrics, cost tracking, and dashboard."""

import time

import pytest
from fastapi.testclient import TestClient

from review.service.api import create_app
from review.telemetry.cost import calculate_cost_usd
from review.telemetry.dashboard import render_dashboard_html
from review.telemetry.models import ReviewTrace, Span, SpanType
from review.telemetry.registry import MetricsRegistry, get_global_registry
from review.telemetry.tracker import TelemetryTracker


@pytest.fixture(autouse=True)
def clean_global_registry() -> None:
    """Ensure global metrics registry is reset between tests."""
    get_global_registry().clear()


def test_calculate_cost_usd_known_and_fallback_models() -> None:
    # 1. Groq GPT-OSS-120B ($0.15 input / $0.60 output per 1M)
    cost_120b = calculate_cost_usd(
        "openai/gpt-oss-120b", prompt_tokens=100_000, completion_tokens=10_000
    )
    # (100k / 1M * 0.15) = 0.015, (10k / 1M * 0.60) = 0.006 => total 0.021
    assert round(cost_120b, 4) == 0.0210

    # 2. Llama 3.3 70B ($0.59 input / $0.79 output per 1M)
    cost_llama = calculate_cost_usd(
        "llama-3.3-70b-versatile", prompt_tokens=1_000_000, completion_tokens=1_000_000
    )
    assert round(cost_llama, 2) == 1.38

    # 3. Fallback pricing for unlisted model ($0.50 input / $1.00 output per 1M)
    cost_unknown = calculate_cost_usd(
        "unknown-model", prompt_tokens=1_000_000, completion_tokens=1_000_000
    )
    assert round(cost_unknown, 2) == 1.50

    # 4. Zero tokens
    assert calculate_cost_usd("openai/gpt-oss-120b", 0, 0) == 0.0


def test_span_lifecycle_and_timing() -> None:
    span = Span(
        name="test_span",
        span_type=SpanType.LLM_REASONING,
        start_time=time.time() - 0.05,
    )
    assert span.end_time is None
    assert span.duration_ms == 0.0

    span.finish()
    assert span.end_time is not None
    assert span.duration_ms >= 40.0  # at least ~50ms
    assert span.error is None

    # Error span
    err_span = Span(name="fail_span", span_type=SpanType.TOOL_EXECUTION)
    err_span.finish(error="File not found")
    assert err_span.error == "File not found"


def test_telemetry_tracker_lifecycle_and_spans() -> None:
    tracker = TelemetryTracker(repo="pallets/flask", pull_number=42, delivery_id="deliv-123")

    with tracker.span("llm_reasoning_step", SpanType.LLM_REASONING):
        time.sleep(0.01)

    with tracker.span("search_precedents", SpanType.MEMORY):
        pass

    # Exception in span
    with (
        pytest.raises(ValueError, match="Synthetic failure"),
        tracker.span("broken_sandbox", SpanType.SANDBOX),
    ):
        raise ValueError("Synthetic failure")

    assert len(tracker.trace.spans) == 3
    assert tracker.trace.spans[0].span_type == SpanType.LLM_REASONING
    assert tracker.trace.spans[1].span_type == SpanType.MEMORY
    assert tracker.trace.spans[2].span_type == SpanType.SANDBOX
    assert tracker.trace.spans[2].error == "Synthetic failure"

    # Token and cost accumulation
    tracker.record_tokens(prompt_tokens=5000, completion_tokens=500, model="openai/gpt-oss-120b")
    assert tracker.trace.prompt_tokens == 5000
    assert tracker.trace.completion_tokens == 500
    assert tracker.trace.total_tokens == 5500
    assert tracker.trace.estimated_cost_usd > 0.0

    # Verification and reproduction events
    tracker.record_finding_verification(passed=True)
    tracker.record_finding_verification(passed=False)
    tracker.record_reproduction(succeeded=True)
    tracker.record_precedent_citation()

    trace = tracker.finish(status="success", findings_count=2)
    assert trace.status == "success"
    assert trace.findings_count == 2
    assert trace.findings_verified == 2
    assert trace.findings_rejected == 1
    assert trace.reproductions_succeeded == 1
    assert trace.precedents_cited == 1
    assert trace.total_duration_ms > 0.0

    breakdown = trace.get_latency_breakdown()
    assert SpanType.LLM_REASONING.value in breakdown
    assert SpanType.MEMORY.value in breakdown
    assert SpanType.SANDBOX.value in breakdown


def test_metrics_registry_aggregation() -> None:
    registry = MetricsRegistry(max_recent_traces=10)

    # Record first trace
    t1 = ReviewTrace(
        trace_id="t1",
        repo="my-org/repo-a",
        pull_number=10,
        status="success",
        total_duration_ms=2500.0,
        prompt_tokens=1000,
        completion_tokens=200,
        total_tokens=1200,
        estimated_cost_usd=0.005,
        findings_count=3,
        findings_verified=2,
        findings_rejected=1,
        reproductions_attempted=1,
        reproductions_succeeded=1,
        precedents_cited=1,
    )
    registry.record_trace(t1)
    registry.record_finding_severity("high")
    registry.record_finding_severity("high")
    registry.record_finding_severity("medium")

    # Record second trace (empty diff)
    t2 = ReviewTrace(
        trace_id="t2",
        repo="my-org/repo-b",
        pull_number=11,
        status="empty_diff",
        total_duration_ms=100.0,
    )
    registry.record_trace(t2)

    summary = registry.get_summary()
    assert summary["reviews"]["total"] == 2
    assert summary["reviews"]["by_status"]["success"] == 1
    assert summary["reviews"]["by_status"]["empty_diff"] == 1
    assert summary["findings"]["total"] == 3
    assert summary["findings"]["by_severity"]["high"] == 2
    assert summary["findings"]["by_severity"]["medium"] == 1

    # False positive rate: 1 rejected / (2 verified + 1 rejected) = 1/3 ~ 0.3333
    assert round(summary["findings"]["false_positive_filter_rate"], 2) == 0.33
    # Reproduction rate: 1/1 = 1.0
    assert summary["reproduction"]["success_rate"] == 1.0
    # Precedent citation rate: 1/3 ~ 0.3333
    assert round(summary["memory"]["precedent_citation_rate"], 2) == 0.33

    assert summary["resources"]["total_tokens"] == 1200
    assert summary["resources"]["total_cost_usd"] == 0.005

    recent = registry.get_recent_traces(limit=5)
    assert len(recent) == 2
    assert recent[0]["trace_id"] == "t2"  # LIFO order
    assert recent[1]["trace_id"] == "t1"


def test_metrics_registry_prometheus_exposition() -> None:
    registry = MetricsRegistry()
    t = ReviewTrace(
        trace_id="p1",
        repo="test/prom",
        status="success",
        total_duration_ms=1500.0,
        prompt_tokens=500,
        completion_tokens=100,
        total_tokens=600,
        estimated_cost_usd=0.0015,
        findings_count=1,
        findings_verified=1,
        findings_rejected=0,
        reproductions_attempted=1,
        reproductions_succeeded=1,
        precedents_cited=1,
    )
    registry.record_trace(t)
    registry.record_finding_severity("high")

    prom_text = registry.get_prometheus_metrics()
    assert "pr_reviews_total{status=\"success\"} 1" in prom_text
    assert "pr_findings_total{severity=\"high\"} 1" in prom_text
    assert "pr_verifier_verified_total 1" in prom_text
    assert "pr_sandbox_reproductions_total{status=\"succeeded\"} 1" in prom_text
    assert "pr_precedents_cited_total 1" in prom_text
    assert "pr_tokens_consumed_total{type=\"total\"} 600" in prom_text
    assert "pr_cost_usd_total 0.001500" in prom_text
    assert "pr_review_duration_ms_avg 1500.00" in prom_text


def test_dashboard_html_rendering() -> None:
    registry = MetricsRegistry()
    summary = registry.get_summary()
    html_empty = render_dashboard_html(summary=summary, recent_traces=[])
    assert "<title>Autonomous Review Agent | Operational Telemetry</title>" in html_empty
    assert "No reviews recorded yet" in html_empty

    t_dict = {
        "repo": "pallets/flask",
        "pull_number": 99,
        "status": "success",
        "total_duration_ms": 3200.0,
        "estimated_cost_usd": 0.0042,
        "total_tokens": 1500,
        "findings_count": 2,
        "reproductions_succeeded": 1,
        "precedents_cited": 1,
    }
    html_with_traces = render_dashboard_html(summary=summary, recent_traces=[t_dict])
    assert "pallets/flask" in html_with_traces
    assert "#99" in html_with_traces
    assert "3.20s" in html_with_traces
    assert "$0.0042" in html_with_traces


def test_api_telemetry_endpoints() -> None:
    app = create_app()
    client = TestClient(app)

    # Seed global registry with a sample trace
    t = ReviewTrace(
        trace_id="api-test-trace",
        repo="fastapi/sample",
        pull_number=45,
        status="success",
        total_duration_ms=2100.0,
        prompt_tokens=800,
        completion_tokens=150,
        total_tokens=950,
        estimated_cost_usd=0.002,
        findings_count=1,
    )
    get_global_registry().record_trace(t)

    # 1. Prometheus /metrics
    res_prom = client.get("/metrics")
    assert res_prom.status_code == 200
    assert "text/plain" in res_prom.headers["content-type"]
    assert "pr_reviews_total{status=\"success\"} 1" in res_prom.text

    # 2. JSON summary /api/v1/metrics
    res_json = client.get("/api/v1/metrics")
    assert res_json.status_code == 200
    data = res_json.json()
    assert data["reviews"]["total"] == 1
    assert data["resources"]["total_tokens"] == 950

    # 3. Traces list /api/v1/traces
    res_traces = client.get("/api/v1/traces?limit=10")
    assert res_traces.status_code == 200
    traces = res_traces.json()
    assert len(traces) == 1
    assert traces[0]["trace_id"] == "api-test-trace"
    assert traces[0]["repo"] == "fastapi/sample"

    # 4. HTML Dashboard /dashboard
    res_dash = client.get("/dashboard")
    assert res_dash.status_code == 200
    assert "text/html" in res_dash.headers["content-type"]
    assert "fastapi/sample" in res_dash.text
    assert "● LIVE" in res_dash.text
