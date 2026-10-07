# ADR 007: Operational Observability, Real-Time Telemetry, and Metrics Dashboard

**Date:** 2026-10-07  
**Status:** Accepted  

## Context
As an autonomous multi-stage agent conducting mission-critical PR code reviews, production observability is essential to answer key operational questions:
1. **Latency Breakdown:** Where is the agent spending time? (LLM reasoning vs. file reading vs. sandbox test execution vs. memory lookups).
2. **Resource Consumption & Cost:** How many prompt and completion tokens were consumed, and what was the dollar cost per review?
3. **Quality & Yield Tracking:** How many candidate findings were proposed, how many false positives were pruned by the adversarial verifier, how many defects were proven via sandbox reproduction, and how many precedents were cited?
4. **Monitoring & Prometheus Ingestion:** Infrastructure monitoring systems (Datadog, Prometheus, Grafana) need standard exposition formats, while engineering leads need an interactive dashboard to inspect recent PR review traces.

## Decision
We implement a dedicated, thread-safe observability architecture (`src/review/telemetry`):

1. **Structured Span Execution (`Span` & `SpanType`):** Every distinct phase is wrapped in a timed span (`LLM_REASONING`, `TOOL_EXECUTION`, `VERIFIER`, `SANDBOX`, `MEMORY`).
2. **Review Traces (`ReviewTrace`):** Encapsulates the complete lifecycle of a PR review, linking repository, PR number, delivery ID, token counts, dollar cost, duration, and individual spans.
3. **Accurate Cost Engine (`calculate_cost_usd`):** Model-specific token pricing tables normalize cost calculations for Meta Llama and Groq models down to micro-dollar accuracy ($0.000001).
4. **Thread-Safe Metrics Registry (`MetricsRegistry`):** Aggregates counters and durations, computing running false-positive filter rates, sandbox reproduction success rates, precedent citation rates, and latency percentiles ($p50$, $p95$, average).
5. **Standard Prometheus Exposition (`GET /metrics`):** Exports metrics formatted per OpenMetrics specifications for seamless scraping.
6. **REST API Telemetry Endpoints:**
   - `GET /api/v1/metrics`: Structured JSON summary of all operational KPIs.
   - `GET /api/v1/traces`: Historical audit log of recent PR review traces.
7. **Zero-Dependency Dark-Mode Dashboard (`GET /dashboard`):** A self-contained, responsive HTML console providing real-time KPI status cards, latency breakdowns, and recent PR audit trails without external CDN dependencies.

## Consequences
- Full transparency into inference costs, turnaround latency, and defect yield.
- Immediate detection of performance bottlenecks or budget spikes.
- Complete compatibility with standard cloud observability platforms.
