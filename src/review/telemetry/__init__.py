"""Telemetry, observability, distributed tracing, cost tracking, and metrics monitoring."""

from review.telemetry.console import render_dashboard_html
from review.telemetry.cost import calculate_cost_usd
from review.telemetry.models import ReviewTrace, Span, SpanType
from review.telemetry.otel import (
    ConsoleSpanExporter,
    InMemorySpanExporter,
    OpenTelemetryMiddleware,
    OtelSpan,
    OtlpHttpSpanExporter,
    SimpleSpanProcessor,
    SpanContext,
    SpanEvent,
    SpanExporter,
    SpanKind,
    SpanProcessor,
    SpanStatus,
    StatusCode,
    Tracer,
    TracerProvider,
    extract_traceparent,
    get_current_span,
    get_tracer,
    get_tracer_provider,
    inject_traceparent,
    set_tracer_provider,
    trace,
)
from review.telemetry.registry import MetricsRegistry, get_global_registry
from review.telemetry.tracker import TelemetryTracker

__all__ = [
    "ConsoleSpanExporter",
    "InMemorySpanExporter",
    "MetricsRegistry",
    "OpenTelemetryMiddleware",
    "OtelSpan",
    "OtlpHttpSpanExporter",
    "ReviewTrace",
    "SimpleSpanProcessor",
    "Span",
    "SpanContext",
    "SpanEvent",
    "SpanExporter",
    "SpanKind",
    "SpanProcessor",
    "SpanStatus",
    "SpanType",
    "StatusCode",
    "TelemetryTracker",
    "Tracer",
    "TracerProvider",
    "calculate_cost_usd",
    "extract_traceparent",
    "get_current_span",
    "get_global_registry",
    "get_tracer",
    "get_tracer_provider",
    "inject_traceparent",
    "render_dashboard_html",
    "set_tracer_provider",
    "trace",
]
