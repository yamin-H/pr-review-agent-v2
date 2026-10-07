# Autonomous PR Review Agent: Project Roadmap & Milestone Plan

**Status:** Active  
**Current Phase:** Phase 6 (Production Infrastructure & Service Backbone)

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
| **Phase 7** | **Advanced Agent Investigation Tools** | AST impact mapping (`impact_map.py`), static analysis (`static_analysis.py`), git blame tool (`get_blame`), subagent delegation | ⏳ *Next Phase* |
| **Phase 8** | **Advanced Repository Memory & Signals** | `pgvector` HNSW semantic search, human reaction ingestion (`signals.py`), calibrated silence heuristic (`calibration.py`) | 📋 *Backlog* |
| **Phase 9** | **Observability & Web Console** | Dedicated Next.js web application (`apps/web/`), Langfuse/OpenTelemetry distributed tracing, operations runbook (`runbook.md`) | 📋 *Backlog* |

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
