# Autonomous PR Review Agent: Project Roadmap & Milestone Plan

**Status:** Active  
**Current Phase:** Phase 9 (Observability, Web Console & Operations Runbook) — Complete  
**Next Phase:** Production Launch & Evaluation

---

## 1. Phase Milestones & Status

| Phase | Description | Deliverables | Status |
| :---: | :--- | :--- | :---: |
| **Phase 0** | **Setup & Tooling** | `pyproject.toml`, `.gitignore`, `LICENSE`, CI workflow, Dependabot | ✅ **DONE** |
| **Phase 1** | **Deterministic Review Engine** | Hunk parsing (`diff.py`), added-line index, file filtering (`filters.py`), line validation (`validate.py`), prompt boundary isolation (`security.py`), CLI runner (`cli.py`) | ✅ **DONE** |
| **Phase 2** | **Empirical Evaluation Suite** | SZZ algorithm mining (`build_szz.py`), PR collector (`collect_prs.py`), metrics engine (`metrics.py`), benchmark runner (`evals/run.py`), benchmark logs | ✅ **DONE** |
| **Phase 3** | **Autonomous Agent Loop** | Plan-Act-Observe cycle (`graph.py`), state tracing (`state.py`), budgets & loop detection (`budgets.py`), read-only tools (`diff_tools.py`, `repo_tools.py`), scripted `FakeLLM` | ✅ **DONE** |
| **Phase 4** | **Adversarial Finding Verifier** | Devil's Advocate refutation engine (`verifier.py`), tri-state verdicts (`keep`, `drop`, `revise`), line fallback, on-demand agent tool & CLI `--verify` pass | ✅ **DONE** |
| **Phase 5** | **Proof by Execution Sandbox** | Test synthesizer (`synthesizer.py`), timeout runner (`runner.py`), proof engine (`prover.py`), evidence attaching, agent tool & CLI `--reproduce` pass | ✅ **DONE** |
| **Phase 6** | **Production Infrastructure & Service Backbone** | PostgreSQL 16 (`pgvector`) & Redis 7 (`docker-compose.yml`), SQLAlchemy 2.0 async models (`models.py`, `session.py`), Alembic migrations, decoupled `arq` Redis queue worker (`worker/jobs.py`), modular FastAPI routes (`api/routes/`), multi-tenant isolation tests, architecture & STRIDE threat model docs | ✅ **DONE** |
| **Phase 7** | **Advanced Agent Investigation Tools** | AST impact mapping (`impact_map.py`), static analysis (`static_analysis.py`), git blame tool (`get_blame`), memory tools modularization, subagent delegation (`subagent.py`) | ✅ **DONE** |
| **Phase 8** | **Advanced Repository Memory & Signals** | `pgvector` HNSW semantic search, human reaction ingestion (`signals.py`), calibrated silence heuristic (`calibration.py`) | ✅ **DONE** |
| **Phase 9** | **Observability, Web Console & Operations Runbook** | OpenTelemetry distributed tracing (`otel.py`), W3C TraceContext propagation, OTLP HTTP exporter, 4-tab Web Console & Waterfall Inspector (`console.py`), ADR-008, Operations Runbook (`runbook.md`) | ✅ **DONE** |

---

## 2. Phase 6 Deliverables Completed

1. **Live Local Infrastructure (`docker-compose.yml`):**
   - PostgreSQL 16 container with `pgvector` extension enabled (`review_agent_postgres`).
   - Redis 7 container with health checks (`review_agent_redis`).
2. **Asynchronous Database Layer (`src/review/db/`):**
   - Declarative SQLAlchemy 2.0 models: `Tenant`, `Repository`, `ReviewRun`, `FindingRecord`, `ExecutionTrace`, `RepoPrecedent`.
   - Async connection pooling and session management with `asyncpg` and SQLite `aiosqlite` support.
   - Alembic migrations: `alembic.ini` and `001_initial_schema.py` verified against live PostgreSQL.
3. **Decoupled Queue Worker (`src/review/worker/`):**
   - `arq` Redis worker with deterministic idempotency keys (`review:{owner}/{repo}:{pull_number}:{head_sha}`).
   - Comprehensive review lifecycle: fetch diff -> agent loop -> verifier -> sandbox proof -> post GitHub review -> persist records and traces.
4. **Modular REST & Webhook API (`src/review/api/`):**
   - Router separation: `routes/webhooks.py`, `routes/reviews.py`, `routes/repos.py`.
   - Dependency injection (`deps.py`) and HMAC signature validation (`auth.py`).
5. **Security & System Documentation:**
   - STRIDE threat model document (`docs/threat-model.md`).
   - System architecture and sequence diagrams (`docs/architecture.md`).
   - Automated dependency scanning (`.github/dependabot.yml`).

---

## 3. Phase 7 Deliverables Completed

1. **AST Impact Mapping (`src/review/agent/tools/impact_map.py`):**
   - Static call graph generator tracking direct and transitive callers across changed definitions.
   - Python AST visitor mapping function and class relationships.
2. **Static Analysis & Linter Integration (`src/review/agent/tools/static_analysis.py`):**
   - Native integration with `ruff` and `mypy` formatting findings into review tool inputs.
3. **Git Blame Investigation (`src/review/agent/tools/repo_tools.py`):**
   - Added `get_blame` inspection tool retrieving commit author, date, and historical context.
4. **Investigation Subagent (`src/review/agent/tools/subagent.py`):**
   - Targeted subagent runner executing deep-dive investigation branches with scoped token limits.

---

## 4. Phase 8 Deliverables Completed

1. **Vector Storage & Semantic Precedent Search (`src/review/memory/store.py`):**
   - `TextEmbedder`: Deterministic 384-dimensional unit-normalized semantic embedding vectorizer.
   - `PgVectorMemoryStore`: PostgreSQL 16 pgvector async store utilizing HNSW cosine distance indexing (`vector_cosine_ops`).
   - `VectorType`: Cross-dialect SQLAlchemy type rendering native `Vector(384)` on PostgreSQL and `JSON` on SQLite for instant local test execution.
   - Semantic deduplication: merges citations when cosine similarity $> 0.85$ rather than creating duplicate entries.
2. **Precedent Mining Pipeline (`src/review/memory/precedents.py`):**
   - Git commit history mining extracting CVE identifiers (`CVE-\d{4}-\d+`), PR links, and diff file patterns.
   - Review comment convention extraction identifying team standards and guidelines.
   - SZZ benchmark case bulk ingestion into memory.
   - Backward compatibility preserved via `review.memory.miner.PrecedentMiner`.
3. **Developer Feedback Signals (`src/review/memory/signals.py`):**
   - `SignalFeedback` model and `SignalTracker` engine.
   - GitHub reaction classification: positive (`+1`, `heart`, `rocket`, `hooray`), negative (`-1`, `confused`), neutral (`eyes`, `laugh`).
   - Review discussion action tracking (`resolved`, `dismissed`, `outdated`).
   - Multi-tier metric aggregation: repository-wide, rule category, and rule ID.
4. **Calibrated Silence Engine (`src/review/memory/calibration.py`):**
   - Suppresses noisy findings when historical acceptance rate $A < 0.40$ and signal threshold is reached.
   - Degrades marginal findings ($0.40 \le A < 0.70$) to `Severity.LOW` with historical calibration context.
   - Absolute exemptions: sandbox-reproduced bugs (`is_reproduced=True`) and precedent-cited findings are never silenced.
   - Pipeline integration into `src/review/worker/jobs.py` before GitHub publication.
5. **REST API & Webhooks (`src/review/api/routes/`):**
   - `POST /api/v1/repos/{repo_id}/feedback`: Submits developer feedback signals and recalculates calibration metrics.
   - `GET /api/v1/repos/{repo_id}/calibration`: Exposes acceptance rates and silence thresholds.
   - `POST /api/v1/repos/{repo_id}/precedents` & `GET /api/v1/repos/{repo_id}/precedents`: Semantic search and management.
   - Automatic webhook ingestion for GitHub `pull_request_review_comment` and `reaction` events in `routes/webhooks.py`.


---

## 5. Phase 9 Deliverables Completed

1. **OpenTelemetry Distributed Tracing (`src/review/telemetry/otel.py`):**
   - Full implementation of W3C TraceContext standards (`traceparent`, `tracestate`).
   - Core tracing engine: `SpanContext`, `SpanKind`, `StatusCode`, `SpanStatus`, `SpanEvent`, `OtelSpan`, `Tracer`, and `TracerProvider`.
   - Pluggable exporters: `InMemorySpanExporter` (for automated testing), `ConsoleSpanExporter` (for CLI & logging), and `OtlpHttpSpanExporter` (for Jaeger, Datadog, SigNoz, Langfuse).
   - Async & thread-safe context propagation via `contextvars.ContextVar`.
   - Function tracing decorator `@trace` for synchronous and asynchronous execution.
   - ASGI Middleware (`OpenTelemetryMiddleware`) automatically extracting incoming W3C traceparents and establishing root `SERVER` spans.
   - Seamless bridge between internal `TelemetryTracker.span()` and OpenTelemetry spans.
   - Tracing instrumentation across `GroqReviewer.review_chunk()`, `FindingVerifier.verify_findings()`, and `ProofEngine.attempt_reproduction()`.

2. **Interactive Multi-Tab Web Console & Trace Inspector (`src/review/telemetry/console.py`):**
   - Zero-external-dependency, self-contained HTML/CSS/JS architecture running completely offline and air-gapped.
   - **Tab 1 (System Overview & KPIs):** Real-time monitoring cards for review throughput, precision filter rates, sandbox reproduction proofs, memory precedents, cumulative token costs, and latency percentiles ($p50$, $p95$, $p99$).
   - **Tab 2 (Reviews & Visual Waterfall Trace Inspector):** Filterable review run table with an interactive modal displaying a horizontal waterfall timeline of all execution spans, span durations, and inline findings with sandbox reproduction stdout/stderr traces.
   - **Tab 3 (Repositories & Settings):** Real-time repository management table with live active toggles, sensitivity selectors, and REST API updates (`PUT /api/v1/repos/{owner}/{repo}/settings`).
   - **Tab 4 (Memory & Signals Calibration):** Live semantic vector search against `pgvector` memory and feedback calibration inspector with one-click rule silence overrides (`POST /api/v1/repos/{repo_id}/calibration/{metric_id}/toggle-silence`).

3. **Production Operations Runbook (`docs/runbook.md`):**
   - Deployment topology with multi-container topology and environment variable matrix.
   - Database administration: Alembic zero-downtime migrations, `pg_dump -Fc` backup and restore preserving HNSW vector indexes, and routine `VACUUM` / `REINDEX`.
   - Key rotation runbooks for GitHub App private keys (`.pem`), webhook secrets, and database credentials.
   - Incident response playbooks for false-positive spikes, worker queue backlogs, LLM rate limiting, and sandbox timeouts.
   - Prometheus alerting rules specification (`ReviewAgentHighErrorRate`, `ReviewAgentQueueLag`, `ReviewAgentHighP99Latency`).

4. **Architectural Decision Record (`docs/adr/008-distributed-tracing-and-observability.md`):**
   - Comprehensive documentation of architectural choices, trade-offs, and design rationale for OpenTelemetry tracing and self-contained web console.
