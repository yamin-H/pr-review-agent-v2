# Autonomous PR Review Agent: Project Roadmap & Milestone Plan

**Status:** Active  
**Current Phase:** Phase 8 (Advanced Repository Memory & Signals) — Complete  
**Next Phase:** Phase 9 (Observability & Web Console)

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
| **Phase 9** | **Observability & Web Console** | Dedicated Next.js web application (`apps/web/`), Langfuse/OpenTelemetry distributed tracing, operations runbook (`runbook.md`) | ⏳ *Next Phase* |

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
