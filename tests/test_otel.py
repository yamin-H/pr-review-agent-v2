"""Unit and integration tests for OpenTelemetry distributed tracing."""

import io
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from review.telemetry.models import SpanType
from review.telemetry.otel import (
    ConsoleSpanExporter,
    InMemorySpanExporter,
    OpenTelemetryMiddleware,
    OtlpHttpSpanExporter,
    SimpleSpanProcessor,
    SpanContext,
    SpanKind,
    StatusCode,
    TracerProvider,
    extract_traceparent,
    generate_span_id,
    generate_trace_id,
    get_current_span,
    get_tracer,
    inject_traceparent,
    set_tracer_provider,
    trace,
)
from review.telemetry.tracker import TelemetryTracker


def test_w3c_traceparent_parsing_and_injection() -> None:
    """Test W3C TraceContext parsing, validation rules, and injection."""
    valid_header = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    headers = {"traceparent": valid_header, "tracestate": "congo=t61rcWkgMzE"}

    ctx = extract_traceparent(headers)
    assert ctx is not None
    assert ctx.is_valid is True
    assert ctx.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert ctx.span_id == "00f067aa0ba902b7"
    assert ctx.trace_flags == 1
    assert ctx.is_sampled is True
    assert ctx.trace_state == "congo=t61rcWkgMzE"
    assert ctx.is_remote is True

    # Injection test
    out_headers: dict[str, str] = {}
    inject_traceparent(ctx, out_headers)
    assert out_headers["traceparent"] == valid_header
    assert out_headers["tracestate"] == "congo=t61rcWkgMzE"


def test_w3c_traceparent_invalidation_rules() -> None:
    """Test edge cases: all-zeros trace/span IDs, invalid version, and malformed headers."""
    # Version ff is invalid according to W3C
    assert (
        extract_traceparent(
            {"traceparent": "ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"}
        )
        is None
    )

    # All zeros trace_id is invalid
    assert (
        extract_traceparent(
            {"traceparent": "00-00000000000000000000000000000000-00f067aa0ba902b7-01"}
        )
        is None
    )

    # All zeros span_id is invalid
    assert (
        extract_traceparent(
            {"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01"}
        )
        is None
    )

    # Non-hex characters
    assert (
        extract_traceparent(
            {"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e473g-00f067aa0ba902b7-01"}
        )
        is None
    )

    # Missing header
    assert extract_traceparent({}) is None


def test_span_lifecycle_and_events() -> None:
    """Test span attributes, exception recording, and timing."""
    ctx = SpanContext(
        trace_id=generate_trace_id(),
        span_id=generate_span_id(),
        trace_flags=1,
    )
    provider = TracerProvider()
    tracer = provider.get_tracer("test.module")

    span = tracer.start_span("test_operation", kind=SpanKind.INTERNAL, parent=ctx)
    assert span.is_recording() is True
    assert span.parent_span_id == ctx.span_id
    assert span.context.trace_id == ctx.trace_id

    span.set_attribute("env", "testing")
    span.set_attributes({"repo": "owner/repo", "attempt": 1})
    span.add_event("cache_miss", {"key": "123"})
    assert len(span.events) == 1
    assert span.events[0].name == "cache_miss"

    # Record exception
    exc = ValueError("Simulated operational error")
    span.record_exception(exc)
    assert span.status.status_code == StatusCode.ERROR
    assert span.status.description == "Simulated operational error"
    assert len(span.events) == 2
    assert span.events[1].name == "exception"

    span.end()
    assert span.is_recording() is False
    assert span.duration_ms >= 0.0

    # Serialization tests
    d = span.to_dict()
    assert d["name"] == "test_operation"
    assert d["status"]["status_code"] == "ERROR"

    otlp = span.to_otlp()
    assert otlp["name"] == "test_operation"
    assert otlp["traceId"] == ctx.trace_id
    assert otlp["status"]["code"] == 2


def test_in_memory_exporter_and_context_propagation() -> None:
    """Test parent-child span nesting and InMemorySpanExporter collection."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    set_tracer_provider(provider)

    tracer = get_tracer("test.pipeline")

    with tracer.start_as_current_span("root_job", kind=SpanKind.SERVER) as root:
        assert get_current_span() is root
        root.set_attribute("stage", "start")

        with tracer.start_as_current_span("child_task", kind=SpanKind.INTERNAL) as child:
            assert get_current_span() is child
            assert child.parent_span_id == root.context.span_id
            assert child.context.trace_id == root.context.trace_id
            child.set_attribute("result", "computed")

        assert get_current_span() is root

    finished = exporter.get_finished_spans()
    assert len(finished) == 2
    # Child ends first, then root
    assert finished[0].name == "child_task"
    assert finished[1].name == "root_job"
    assert finished[0].parent_span_id == finished[1].context.span_id

    exporter.clear()
    assert len(exporter.get_finished_spans()) == 0


def test_console_span_exporter() -> None:
    """Test ConsoleSpanExporter formats output correctly to an IO stream."""
    buffer = io.StringIO()
    exporter = ConsoleSpanExporter(stream=buffer)

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test.console")

    with tracer.start_as_current_span("console_span", attributes={"version": 1}):
        pass

    output = buffer.getvalue()
    assert "[OTel Trace] console_span" in output
    assert "duration=" in output
    assert "[OK]" in output


def test_otlp_http_exporter_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test OtlpHttpSpanExporter serializes spans to OTLP JSON payload."""
    sent_requests: list[dict[str, Any]] = []

    def mock_post(self: Any, url: str, json: Any, headers: Any) -> Any:
        sent_requests.append({"url": url, "json": json, "headers": headers})

        class MockResponse:
            status_code = 200
            text = "OK"

        return MockResponse()

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    exporter = OtlpHttpSpanExporter(endpoint="http://collector.local:4318/v1/traces")
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test.otlp")

    with tracer.start_as_current_span("otlp_task", attributes={"service": "agent"}):
        pass

    assert len(sent_requests) == 1
    assert sent_requests[0]["url"] == "http://collector.local:4318/v1/traces"
    payload = sent_requests[0]["json"]
    assert "resourceSpans" in payload
    spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert len(spans) == 1
    assert spans[0]["name"] == "otlp_task"
    exporter.shutdown()


def test_trace_decorator_sync_and_async() -> None:
    """Test @trace decorator on synchronous and asynchronous functions."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    set_tracer_provider(provider)

    @trace("custom_sync_func", attributes={"tag": "sync"})
    def run_sync(x: int) -> int:
        return x * 2

    @trace(attributes={"tag": "async"})
    async def run_async(y: str) -> str:
        return y.upper()

    assert run_sync(21) == 42
    import asyncio

    res = asyncio.run(run_async("hello"))
    assert res == "HELLO"

    spans = exporter.get_finished_spans()
    assert len(spans) == 2
    assert spans[0].name == "custom_sync_func"
    assert spans[0].attributes["tag"] == "sync"
    assert "run_async" in spans[1].name
    assert spans[1].attributes["tag"] == "async"


def test_telemetry_tracker_bridges_to_otel() -> None:
    """Test that TelemetryTracker.span generates nested OpenTelemetry spans."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    set_tracer_provider(provider)

    tracker = TelemetryTracker(repo="test/repo", pull_number=42)

    with tracker.span("verifier_stage", SpanType.VERIFIER, attributes={"rules": 5}):
        pass

    trace_rec = tracker.finish()
    assert len(trace_rec.spans) == 1
    assert trace_rec.spans[0].name == "verifier_stage"

    # Verify OpenTelemetry export
    finished_otel = exporter.get_finished_spans()
    assert len(finished_otel) == 1
    assert finished_otel[0].name == "verifier_stage"
    assert finished_otel[0].attributes["span_type"] == "verifier"
    assert finished_otel[0].attributes["repo"] == "test/repo"
    assert finished_otel[0].attributes["rules"] == 5


def test_fastapi_opentelemetry_middleware() -> None:
    """Test ASGI OpenTelemetryMiddleware intercepts requests and injects trace headers."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    set_tracer_provider(provider)

    app = FastAPI()
    app.add_middleware(OpenTelemetryMiddleware)

    @app.get("/test-route")
    async def sample_endpoint() -> dict[str, str]:
        current = get_current_span()
        trace_id = current.context.trace_id if current else "none"
        return {"status": "ok", "trace_id": trace_id}

    client = TestClient(app)

    # 1. Without incoming traceparent (new root trace)
    resp = client.get("/test-route")
    assert resp.status_code == 200
    assert "traceparent" in resp.headers
    traceparent_hdr = resp.headers["traceparent"]
    assert resp.json()["trace_id"] in traceparent_hdr

    # 2. With incoming W3C traceparent (propagated trace)
    incoming_parent = "00-5c6a1d84949b49a5b3a37d8e9e1c2d3e-1a2b3c4d5e6f7a8b-01"
    resp2 = client.get("/test-route", headers={"traceparent": incoming_parent})
    assert resp2.status_code == 200
    assert "traceparent" in resp2.headers
    assert "5c6a1d84949b49a5b3a37d8e9e1c2d3e" in resp2.headers["traceparent"]
    assert resp2.json()["trace_id"] == "5c6a1d84949b49a5b3a37d8e9e1c2d3e"

    spans = exporter.get_finished_spans()
    assert len(spans) == 2
    assert spans[1].parent_span_id == "1a2b3c4d5e6f7a8b"
    assert spans[1].attributes["http.target"] == "/test-route"
    assert spans[1].attributes["http.status_code"] == 200
