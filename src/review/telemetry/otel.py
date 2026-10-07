"""OpenTelemetry distributed tracing engine, W3C TraceContext propagation, and OTLP exporters."""

import functools
import inspect
import logging
import os
import re
import sys
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from typing import Any, TypeVar, cast

import httpx
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("review.telemetry.otel")

F = TypeVar("F", bound=Callable[..., Any])

# W3C TraceContext Specification regex patterns
# Syntax: {version:2hex}-{trace_id:32hex}-{parent_id:16hex}-{trace_flags:2hex}
TRACEPARENT_HEADER = "traceparent"
TRACESTATE_HEADER = "tracestate"
W3C_TRACEPARENT_REGEX = re.compile(
    r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$", re.IGNORECASE
)
INVALID_TRACE_ID = "0" * 32
INVALID_SPAN_ID = "0" * 16


def generate_trace_id() -> str:
    """Generate a random 16-byte (32-character hex) trace ID."""
    return uuid.uuid4().hex.lower()


def generate_span_id() -> str:
    """Generate a random 8-byte (16-character hex) span ID."""
    return uuid.uuid4().hex[:16].lower()


class SpanKind(StrEnum):
    """OpenTelemetry span category indicating client, server, or internal work."""

    INTERNAL = "INTERNAL"
    SERVER = "SERVER"
    CLIENT = "CLIENT"
    PRODUCER = "PRODUCER"
    CONSUMER = "CONSUMER"


class StatusCode(StrEnum):
    """OpenTelemetry status codes representing execution outcome."""

    UNSET = "UNSET"
    OK = "OK"
    ERROR = "ERROR"


class SpanContext(BaseModel):
    """Immutable context identifying a trace and span according to W3C TraceContext."""

    trace_id: str
    span_id: str
    trace_flags: int = 1  # 0x01 = sampled
    trace_state: str = ""
    is_remote: bool = False

    @property
    def is_valid(self) -> bool:
        """Validate trace and span IDs conform to W3C length and non-zero requirements."""
        return (
            len(self.trace_id) == 32
            and self.trace_id != INVALID_TRACE_ID
            and len(self.span_id) == 16
            and self.span_id != INVALID_SPAN_ID
        )

    @property
    def is_sampled(self) -> bool:
        """Return True if sampled flag is set."""
        return bool(self.trace_flags & 0x01)


class SpanStatus(BaseModel):
    """Status representing span completion state."""

    status_code: StatusCode = StatusCode.UNSET
    description: str | None = None


class SpanEvent(BaseModel):
    """Timed annotation on a span with optional descriptive attributes."""

    name: str
    timestamp: float = Field(default_factory=time.time)
    attributes: dict[str, Any] = Field(default_factory=dict)


def extract_traceparent(headers: Mapping[str, str]) -> SpanContext | None:
    """Extract W3C traceparent header and parse into a valid SpanContext."""
    header_val: str | None = None
    for k, v in headers.items():
        if k.lower() == TRACEPARENT_HEADER:
            header_val = v.strip()
            break

    if not header_val:
        return None

    match = W3C_TRACEPARENT_REGEX.match(header_val)
    if not match:
        return None

    version, trace_id, span_id, flags_hex = match.groups()
    if version == "ff":
        return None
    if trace_id == INVALID_TRACE_ID or span_id == INVALID_SPAN_ID:
        return None

    try:
        flags = int(flags_hex, 16)
    except ValueError:
        return None

    # Optional tracestate header
    trace_state = ""
    for k, v in headers.items():
        if k.lower() == TRACESTATE_HEADER:
            trace_state = v.strip()
            break

    return SpanContext(
        trace_id=trace_id.lower(),
        span_id=span_id.lower(),
        trace_flags=flags,
        trace_state=trace_state,
        is_remote=True,
    )


def inject_traceparent(
    context: SpanContext,
    headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """Inject W3C traceparent and tracestate headers into an outgoing headers dictionary."""
    out = headers if headers is not None else {}
    if not context.is_valid:
        return out

    flags_hex = f"{context.trace_flags:02x}"
    out[TRACEPARENT_HEADER] = f"00-{context.trace_id}-{context.span_id}-{flags_hex}"
    if context.trace_state:
        out[TRACESTATE_HEADER] = context.trace_state
    return out


class OtelSpan:
    """Active or completed OpenTelemetry-compatible span."""

    def __init__(
        self,
        name: str,
        context: SpanContext,
        parent_span_id: str | None = None,
        kind: SpanKind = SpanKind.INTERNAL,
        start_time: float | None = None,
        attributes: dict[str, Any] | None = None,
        tracer: Any | None = None,
    ) -> None:
        self.name = name
        self.context = context
        self.parent_span_id = parent_span_id
        self.kind = kind
        self.start_time = start_time if start_time is not None else time.time()
        self.end_time: float | None = None
        self.attributes: dict[str, Any] = dict(attributes) if attributes else {}
        self.events: list[SpanEvent] = []
        self.status = SpanStatus()
        self._tracer = tracer
        self._ended: bool = False

    @property
    def duration_ms(self) -> float:
        """Elapsed duration in milliseconds."""
        end = self.end_time if self.end_time is not None else time.time()
        return max(0.0, (end - self.start_time) * 1000.0)

    def is_recording(self) -> bool:
        """Return True if span has not ended."""
        return not self._ended

    def set_attribute(self, key: str, value: Any) -> "OtelSpan":
        """Set a single metadata attribute on the span."""
        if self._ended:
            return self
        self.attributes[key] = value
        return self

    def set_attributes(self, attributes: Mapping[str, Any]) -> "OtelSpan":
        """Set multiple metadata attributes on the span."""
        if self._ended:
            return self
        self.attributes.update(attributes)
        return self

    def add_event(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
        timestamp: float | None = None,
    ) -> "OtelSpan":
        """Add a timed event annotation to the span."""
        if self._ended:
            return self
        ts = timestamp if timestamp is not None else time.time()
        self.events.append(SpanEvent(name=name, timestamp=ts, attributes=attributes or {}))
        return self

    def set_status(
        self,
        status_code: StatusCode,
        description: str | None = None,
    ) -> "OtelSpan":
        """Set span status code and optional failure description."""
        if self._ended:
            return self
        self.status = SpanStatus(status_code=status_code, description=description)
        return self

    def record_exception(self, exception: BaseException) -> "OtelSpan":
        """Record an exception event and set status to ERROR."""
        if self._ended:
            return self
        self.add_event(
            name="exception",
            attributes={
                "exception.type": type(exception).__name__,
                "exception.message": str(exception),
            },
        )
        self.set_status(StatusCode.ERROR, description=str(exception))
        return self

    def end(self, end_time: float | None = None) -> None:
        """Complete the span execution."""
        if self._ended:
            return
        self.end_time = end_time if end_time is not None else time.time()
        self._ended = True
        if self.status.status_code == StatusCode.UNSET:
            self.status.status_code = StatusCode.OK

        # Notify active TracerProvider
        if self._tracer is not None and hasattr(self._tracer, "provider"):
            self._tracer.provider.on_span_end(self)
        else:
            provider = get_tracer_provider()
            provider.on_span_end(self)

    def to_dict(self) -> dict[str, Any]:
        """Convert span to standard dictionary format."""
        return {
            "name": self.name,
            "context": self.context.model_dump(),
            "parent_span_id": self.parent_span_id,
            "kind": self.kind.value,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": round(self.duration_ms, 3),
            "attributes": self.attributes,
            "events": [e.model_dump() for e in self.events],
            "status": self.status.model_dump(),
        }

    def to_otlp(self) -> dict[str, Any]:
        """Format span into standard OpenTelemetry OTLP JSON representation."""
        start_nano = int(self.start_time * 1e9)
        end_nano = int((self.end_time if self.end_time else time.time()) * 1e9)

        otlp_attrs: list[dict[str, Any]] = []
        for k, v in self.attributes.items():
            val_dict: dict[str, Any]
            if isinstance(v, bool):
                val_dict = {"boolValue": v}
            elif isinstance(v, int):
                val_dict = {"intValue": str(v)}
            elif isinstance(v, float):
                val_dict = {"doubleValue": v}
            else:
                val_dict = {"stringValue": str(v)}
            otlp_attrs.append({"key": k, "value": val_dict})

        status_code_num = 0
        if self.status.status_code == StatusCode.OK:
            status_code_num = 1
        elif self.status.status_code == StatusCode.ERROR:
            status_code_num = 2

        return {
            "traceId": self.context.trace_id,
            "spanId": self.context.span_id,
            "parentSpanId": self.parent_span_id or "",
            "name": self.name,
            "kind": self.kind.value,
            "startTimeUnixNano": str(start_nano),
            "endTimeUnixNano": str(end_nano),
            "attributes": otlp_attrs,
            "status": {
                "code": status_code_num,
                "message": self.status.description or "",
            },
        }


# Context variable tracking the currently active span in the async/thread context
_current_span_var: ContextVar[OtelSpan | None] = ContextVar("current_span", default=None)


def get_current_span() -> OtelSpan | None:
    """Retrieve the currently active span in the current execution context."""
    return _current_span_var.get()


class SpanExporter(ABC):
    """Abstract interface for shipping finished spans to backends or storage."""

    @abstractmethod
    def export(self, spans: Sequence[OtelSpan]) -> bool:
        """Export finished spans. Returns True on success."""
        pass

    @abstractmethod
    def shutdown(self) -> None:
        """Release underlying connections and resources."""
        pass


class InMemorySpanExporter(SpanExporter):
    """In-memory span exporter for testing and trace verification."""

    def __init__(self) -> None:
        self.spans: list[OtelSpan] = []

    def export(self, spans: Sequence[OtelSpan]) -> bool:
        self.spans.extend(spans)
        return True

    def get_finished_spans(self) -> list[OtelSpan]:
        """Return all recorded finished spans."""
        return list(self.spans)

    def clear(self) -> None:
        """Clear all stored spans."""
        self.spans.clear()

    def shutdown(self) -> None:
        self.clear()


class ConsoleSpanExporter(SpanExporter):
    """Exports human-readable formatted spans to an output stream (default stdout)."""

    def __init__(self, stream: Any | None = None) -> None:
        self.stream = stream or sys.stdout

    def export(self, spans: Sequence[OtelSpan]) -> bool:
        for s in spans:
            status_badge = f"[{s.status.status_code}]"
            parent = f" parent={s.parent_span_id}" if s.parent_span_id else ""
            trace_str = f"trace_id={s.context.trace_id} span_id={s.context.span_id}"
            line = (
                f"[OTel Trace] {s.name} ({s.kind}) duration={s.duration_ms:.2f}ms "
                f"{trace_str}{parent} {status_badge}\n"
            )
            try:
                self.stream.write(line)
                self.stream.flush()
            except Exception as e:
                logger.warning("Failed writing span to stream: %s", e)
        return True

    def shutdown(self) -> None:
        pass


class OtlpHttpSpanExporter(SpanExporter):
    """Exports spans as OTLP JSON HTTP POST requests to collectors (Jaeger, Datadog, SigNoz)."""

    def __init__(
        self,
        endpoint: str | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 5.0,
        service_name: str = "pr-review-agent",
    ) -> None:
        self.endpoint = (
            endpoint
            or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
            or (os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318") + "/v1/traces")
        )
        self.headers = headers or {}
        self.timeout_seconds = timeout_seconds
        self.service_name = service_name
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(timeout=self.timeout_seconds)
        return self._client

    def export(self, spans: Sequence[OtelSpan]) -> bool:
        if not spans:
            return True

        resource_spans = {
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": self.service_name}},
                    {"key": "telemetry.sdk.language", "value": {"stringValue": "python"}},
                ]
            },
            "scopeSpans": [
                {
                    "scope": {"name": "review.telemetry.otel", "version": "1.0.0"},
                    "spans": [s.to_otlp() for s in spans],
                }
            ],
        }

        payload = {"resourceSpans": [resource_spans]}
        req_headers = {"Content-Type": "application/json", **self.headers}

        try:
            client = self._get_client()
            resp = client.post(self.endpoint, json=payload, headers=req_headers)
            if resp.status_code >= 400:
                logger.warning("OTLP export failed with HTTP %d: %s", resp.status_code, resp.text)
                return False
            return True
        except Exception as e:
            logger.debug("Failed exporting spans to OTLP endpoint '%s': %s", self.endpoint, e)
            return False

    def shutdown(self) -> None:
        if self._client and not self._client.is_closed:
            self._client.close()


class SpanProcessor(ABC):
    """Receives span start and end lifecycle callbacks."""

    @abstractmethod
    def on_start(self, span: OtelSpan) -> None:
        pass

    @abstractmethod
    def on_end(self, span: OtelSpan) -> None:
        pass

    @abstractmethod
    def shutdown(self) -> None:
        pass


class SimpleSpanProcessor(SpanProcessor):
    """Synchronous processor forwarding finished spans immediately to exporter."""

    def __init__(self, exporter: SpanExporter) -> None:
        self.exporter = exporter

    def on_start(self, span: OtelSpan) -> None:
        pass

    def on_end(self, span: OtelSpan) -> None:
        self.exporter.export([span])

    def shutdown(self) -> None:
        self.exporter.shutdown()


class Tracer:
    """Creates spans and manages execution context nesting."""

    def __init__(self, name: str, provider: "TracerProvider") -> None:
        self.name = name
        self.provider = provider

    def start_span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: dict[str, Any] | None = None,
        parent: SpanContext | None = None,
    ) -> OtelSpan:
        """Create and start a new OtelSpan."""
        parent_context = parent
        if parent_context is None:
            current = get_current_span()
            if current is not None:
                parent_context = current.context

        trace_id = parent_context.trace_id if parent_context else generate_trace_id()
        parent_span_id = parent_context.span_id if parent_context else None
        span_id = generate_span_id()
        trace_flags = parent_context.trace_flags if parent_context else 1
        trace_state = parent_context.trace_state if parent_context else ""

        ctx = SpanContext(
            trace_id=trace_id,
            span_id=span_id,
            trace_flags=trace_flags,
            trace_state=trace_state,
        )

        merged_attrs = {"tracer.name": self.name}
        if attributes:
            merged_attrs.update(attributes)

        span = OtelSpan(
            name=name,
            context=ctx,
            parent_span_id=parent_span_id,
            kind=kind,
            attributes=merged_attrs,
            tracer=self,
        )
        self.provider.on_span_start(span)
        return span

    @contextmanager
    def start_as_current_span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: dict[str, Any] | None = None,
        parent: SpanContext | None = None,
    ) -> Generator[OtelSpan, None, None]:
        """Context manager setting span as current in ContextVar, ending it upon exit."""
        span = self.start_span(name=name, kind=kind, attributes=attributes, parent=parent)
        token = _current_span_var.set(span)
        try:
            yield span
        except BaseException as exc:
            span.record_exception(exc)
            raise
        finally:
            span.end()
            _current_span_var.reset(token)


class TracerProvider:
    """Manages tracer instances and span processors."""

    def __init__(self) -> None:
        self.processors: list[SpanProcessor] = []
        self._tracers: dict[str, Tracer] = {}

    def add_span_processor(self, processor: SpanProcessor) -> None:
        """Register a new span processor."""
        self.processors.append(processor)

    def get_tracer(self, instrumenting_module_name: str = "review-agent") -> Tracer:
        """Retrieve or create a named Tracer."""
        if instrumenting_module_name not in self._tracers:
            self._tracers[instrumenting_module_name] = Tracer(
                name=instrumenting_module_name,
                provider=self,
            )
        return self._tracers[instrumenting_module_name]

    def on_span_start(self, span: OtelSpan) -> None:
        """Forward span start event to processors."""
        for p in self.processors:
            p.on_start(span)

    def on_span_end(self, span: OtelSpan) -> None:
        """Forward span end event to processors."""
        for p in self.processors:
            p.on_end(span)

    def shutdown(self) -> None:
        """Shutdown all registered processors."""
        for p in self.processors:
            p.shutdown()


_GLOBAL_TRACER_PROVIDER: TracerProvider | None = None


def get_tracer_provider() -> TracerProvider:
    """Retrieve global TracerProvider singleton."""
    global _GLOBAL_TRACER_PROVIDER
    if _GLOBAL_TRACER_PROVIDER is None:
        _GLOBAL_TRACER_PROVIDER = TracerProvider()
    return _GLOBAL_TRACER_PROVIDER


def set_tracer_provider(provider: TracerProvider) -> None:
    """Set global TracerProvider singleton."""
    global _GLOBAL_TRACER_PROVIDER
    _GLOBAL_TRACER_PROVIDER = provider


def get_tracer(name: str = "review-agent") -> Tracer:
    """Convenience helper to obtain a named tracer from global provider."""
    return get_tracer_provider().get_tracer(name)


def trace(
    name: str | None = None,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    """Decorator to trace synchronous or asynchronous function execution."""

    def decorator(fn: F) -> F:
        span_name = name or fn.__qualname__

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                tracer = get_tracer(fn.__module__)
                with tracer.start_as_current_span(span_name, kind=kind, attributes=attributes):
                    return await fn(*args, **kwargs)

            return cast(F, async_wrapper)
        else:

            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                tracer = get_tracer(fn.__module__)
                with tracer.start_as_current_span(span_name, kind=kind, attributes=attributes):
                    return fn(*args, **kwargs)

            return cast(F, sync_wrapper)

    return decorator


class OpenTelemetryMiddleware(BaseHTTPMiddleware):
    """ASGI Middleware extracting W3C TraceContext and creating a root SERVER span."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        tracer = get_tracer("review.api.http")
        parent_context = extract_traceparent(request.headers)

        span_name = f"{request.method} {request.url.path}"
        span_attrs = {
            "http.method": request.method,
            "http.url": str(request.url),
            "http.target": request.url.path,
            "http.client_ip": request.client.host if request.client else "",
            "http.user_agent": request.headers.get("user-agent", ""),
        }

        with tracer.start_as_current_span(
            name=span_name,
            kind=SpanKind.SERVER,
            attributes=span_attrs,
            parent=parent_context,
        ) as span:
            try:
                response = await call_next(request)
                span.set_attribute("http.status_code", response.status_code)
                if response.status_code >= 500:
                    span.set_status(StatusCode.ERROR, f"HTTP {response.status_code}")
                else:
                    span.set_status(StatusCode.OK)

                # Inject traceparent into outgoing response headers
                inject_traceparent(span.context, response.headers)  # type: ignore[arg-type]
                return response
            except Exception as exc:
                span.record_exception(exc)
                raise
