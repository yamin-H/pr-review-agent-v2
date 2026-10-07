# ADR 008: OpenTelemetry Distributed Tracing, Multi-Tab Web Console, and Operations Runbook

**Date:** 2026-10-07  
**Status:** Accepted  

## Context
While ADR 007 established internal review metrics and in-memory trace accounting, operating an autonomous code review agent at enterprise scale demands deep distributed tracing and actionable administrative controls:
1. **End-to-End Distributed Tracing:** Webhook requests originate from GitHub and traverse load balancers, FastAPI API endpoints, asynchronous Redis worker queues, and multi-step agent reasoning passes. Operations teams need standard W3C TraceContext headers (`traceparent`, `tracestate`) to trace requests across the complete distributed topology.
2. **APM Interoperability:** Spans must be exportable to standard OpenTelemetry collectors (Jaeger, Datadog, SigNoz, Langfuse) via the OpenTelemetry Protocol (OTLP/HTTP JSON) without imposing heavyweight external SDK dependencies or proprietary vendor lock-in.
3. **Trace Waterfall Visualization:** When developers dispute a finding or an audit is performed, SREs and engineering managers need a visual timeline showing exactly how long AST analysis took, what prompt tokens were sent to Groq, what the adversarial verifier challenged, and whether the isolated sandbox was able to reproduce the bug.
4. **Interactive Administrative Controls:** Operators require a single pane of glass to inspect registered repositories, adjust verifier sensitivity thresholds, test semantic vector memory queries against PostgreSQL `pgvector`, and manually silence noisy review rules in real time.
5. **Operational Resilience:** Standard operating procedures are needed for zero-downtime secret rotation, automated database backups preserving HNSW vector indexes, and incident response playbooks for false-positive spikes and LLM outages.

## Decision
We implement a comprehensive distributed tracing, administrative console, and operational framework (`review.telemetry.otel`, `review.telemetry.console`, and `docs/runbook.md`):

1. **W3C TraceContext Standard:**
   - Full parser and serializer for standard `traceparent` headers (`{version}-{trace_id}-{span_id}-{trace_flags}`).
   - Propagation of `tracestate` context across incoming webhooks and downstream HTTP requests.
2. **OpenTelemetry Core Implementation (`src/review/telemetry/otel.py`):**
   - Core abstractions: `SpanContext`, `SpanKind` (INTERNAL, SERVER, CLIENT, PRODUCER, CONSUMER), `StatusCode` (UNSET, OK, ERROR), `SpanStatus`, `SpanEvent`, and `OtelSpan`.
   - Context propagation: Thread-safe and async-safe context management via Python's `contextvars.ContextVar`.
   - Function tracing decorator `@trace` supporting both synchronous and asynchronous functions.
   - ASGI Middleware (`OpenTelemetryMiddleware`) automatically establishing root SERVER spans for all incoming HTTP requests.
3. **Pluggable Span Exporters:**
   - `InMemorySpanExporter`: For deterministic unit and integration test assertions.
   - `ConsoleSpanExporter`: Human-readable stderr/stdout logging during development and CLI runs.
   - `OtlpHttpSpanExporter`: Standard OTLP JSON HTTP export to any OpenTelemetry collector endpoint (`/v1/traces`).
4. **TelemetryTracker & Engine Integration:**
   - `TelemetryTracker` bridges internal `ReviewTrace` and OpenTelemetry spans, creating correlated child spans whenever `tracker.span()` is called during agent execution.
   - Tracing instrumentation added to `GroqReviewer.review_chunk()`, `FindingVerifier.verify_findings()`, and `ProofEngine.attempt_reproduction()`.
5. **Multi-Tab Interactive Web Console (`src/review/telemetry/console.py`):**
   - **Tab 1 (System Overview & KPIs):** Real-time KPI cards for review throughput, precision filter rates, sandbox reproduction proofs, memory precedents, cumulative token costs, and latency percentiles ($p50$, $p95$, $p99$).
   - **Tab 2 (Reviews & Visual Waterfall Trace Inspector):** Filterable review run table with an interactive modal displaying a horizontal waterfall timeline of all execution spans, span durations, and inline findings with sandbox reproduction stdout/stderr traces.
   - **Tab 3 (Repositories & Settings):** Real-time repository management table with live active toggles, sensitivity selectors, and REST API updates (`PUT /api/v1/repos/{owner}/{repo}/settings`).
   - **Tab 4 (Memory & Signals Calibration):** Live semantic vector search against `pgvector` memory and feedback calibration inspector with one-click rule silence overrides (`POST /api/v1/repos/{repo_id}/calibration/{metric_id}/toggle-silence`).
   - Zero external CDN dependencies: 100% self-contained HTML/CSS/JavaScript running cleanly in air-gapped environments.
6. **Production Operations Runbook (`docs/runbook.md`):**
   - Deployment architecture with multi-container topology and environment variable matrix.
   - Database administration: Alembic zero-downtime migrations, `pg_dump -Fc` backup and restore preserving HNSW vector indexes, and routine `VACUUM` / `REINDEX`.
   - Key rotation runbooks for GitHub App private keys (`.pem`), webhook secrets, and database credentials.
   - Incident response playbooks for false-positive spikes, worker queue backlogs, LLM rate limiting, and sandbox timeouts.
   - Prometheus alerting rules specification (`ReviewAgentHighErrorRate`, `ReviewAgentQueueLag`, `ReviewAgentHighP99Latency`).

## Consequences
- **Complete End-to-End Tracing:** Any transaction can be correlated from incoming GitHub webhook to background worker execution and LLM inference.
- **Immediate Debuggability:** Visual waterfall timelines eliminate guesswork when diagnosing slow reviews or unexpected verifier behavior.
- **Production Readiness:** Clear operational runbooks and Prometheus alert definitions ensure high availability and smooth incident response for on-call engineers.
- **Air-Gapped & Zero-Dependency:** Console operates reliably without external internet access or CDN availability.
