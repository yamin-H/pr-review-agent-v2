# Autonomous PR Review Agent

A production-grade, multi-tenant autonomous AI agent for GitHub Pull Request code reviews. It analyzes PR diffs, executes multi-step semantic investigations, reproduces prospective issues in sandbox environments, verifies findings with grounded diff validation and precedent memory, and posts structured inline comments with reproduction evidence.

## System Architecture

The agent is organized into modular services and subsystems:

```
                  ┌──────────────────────┐
                  │ GitHub App Webhooks  │
                  └──────────┬───────────┘
                             │ HMAC-SHA256 (Replay protected)
                             ▼
                  ┌──────────────────────┐
                  │  FastAPI API Server  │
                  └──────────┬───────────┘
                             │ Enqueue review job
                             ▼
                  ┌──────────────────────┐
                  │   Redis 7 (arq)      │
                  └──────────┬───────────┘
                             │ Pop idempotent job
                             ▼
                  ┌──────────────────────┐
                  │ Asynchronous Worker  │
                  └──────────┬───────────┘
                             │
     ┌───────────────────────┼───────────────────────┐
     ▼                       ▼                       ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────────────┐
│ Agent Loop   │     │ Sandbox      │     │ PostgreSQL 16        │
│ & Verifier   │     │ Reproducer   │     │ (pgvector + Alembic) │
└──────────────┘     └──────────────┘     └──────────────────────┘
```

For complete architectural details, sequence diagrams, and multi-tenant security guarantees, see [docs/architecture.md](docs/architecture.md) and [docs/threat-model.md](docs/threat-model.md).

---

## Key Features

- **Decoupled Asynchronous Processing**: Fast webhook ingestion (HTTP 202 Accepted) with queue dispatch via `arq` and Redis.
- **Idempotent Job Execution**: Zero duplicate reviews on webhook retries or parallel deliveries keyed on `review:{owner}/{repo}:{pull_number}:{head_sha}`.
- **Multi-Tenant Isolation**: Row-level tenancy isolation separating organizations, repositories, review runs, finding records, traces, and precedents.
- **PostgreSQL 16 with pgvector**: Scalable relational schema with Alembic async migrations and vector extension support for semantic precedents.
- **Autonomous Investigation**: LangGraph-style agent planning, acting, tool usage (`read_file`, `search_code`, `get_diff`, `get_blame`), and bounded cost/step budgets.
- **Self-Healing Finding Verification**: Automated 2nd-stage LLM verification pruning false positives and aligning line numbers to diff hunks.
- **Deterministic Sandbox Reproduction**: Validates bug hypotheses by synthesizing unit tests and running them in an isolated subprocess runner.
- **Precedent Mining & Team Memory**: Stores past CVEs, incident fixes, and team review patterns to maintain contextual consistency.
- **Full Observability & Cost Tracking**: Token accounting, latency breakdown, Prometheus exposition, and telemetry dashboards.

---

## Getting Started

### Prerequisites

- Python 3.11+
- Docker and Docker Compose
- Git

### 1. Environment Configuration

Copy the example environment file and configure credentials:

```bash
cp .env.example .env
```

Key variables:
- `DATABASE_URL`: PostgreSQL connection string (defaults to `postgresql+asyncpg://postgres:postgres@localhost:5432/review_agent`)
- `REDIS_URL`: Redis connection string (defaults to `redis://localhost:6379/0`)
- `GITHUB_APP_ID`: GitHub App ID
- `GITHUB_PRIVATE_KEY_PATH` or `GITHUB_PRIVATE_KEY_PEM`: GitHub App RSA private key
- `GITHUB_WEBHOOK_SECRET`: Secret token for HMAC signature validation
- `GROQ_API_KEY`: API key for LLM inference

### 2. Start Infrastructure Services

Spin up PostgreSQL 16 (with `pgvector`) and Redis 7:

```bash
docker-compose up -d
```

Verify services are healthy:
```bash
docker-compose ps
```

### 3. Run Database Migrations

Apply database schema migrations using Alembic:

```bash
alembic upgrade head
```

### 4. Run the API Service

Launch the FastAPI webhook ingestion and REST server:

```bash
uvicorn review.api.main:create_app --factory --host 0.0.0.0 --port 8000 --reload
```

Interactive API documentation will be accessible at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- Metrics: `http://localhost:8000/api/v1/metrics`
- Prometheus: `http://localhost:8000/metrics`

### 5. Run the Background Queue Worker

Start the decoupled `arq` Redis worker process:

```bash
python -m review.worker.main
```

---

## CLI Usage

You can run reviews directly against local git repositories or saved diff files without running the server:

```bash
# Review a local git repository
python -m review.cli --repo-dir . --max-steps 15

# Review a specific diff file
python -m review.cli --diff-file path/to/patch.diff --output json
```

---

## Development & Testing

Run the full automated test suite:

```bash
pytest
```

Run static type checking (strict mode):

```bash
mypy src tests
```

Run linter checks and formatting:

```bash
ruff check .
```

---

## Project Structure

```
review-agent/
├── docker-compose.yml             # Postgres (pgvector) + Redis
├── alembic.ini                    # Database migration configuration
├── pyproject.toml                 # Package dependencies and tool settings
├── .env.example                   # Environment configuration template
│
├── docs/
│   ├── architecture.md            # C4 architecture, data flows, and isolation
│   ├── threat-model.md            # STRIDE threat model & verified mitigations
│   ├── project-plan.md            # Phase tracking & milestone status
│   └── adr/                       # Architectural Decision Records
│
├── src/
│   └── review/
│       ├── api/                   # FastAPI routes, dependencies, and app factory
│       ├── worker/                # arq Redis queue worker and review jobs
│       ├── db/                    # SQLAlchemy 2.0 models, session, and Alembic versions
│       ├── github/                # App JWT auth, cached client, webhooks, publisher
│       ├── agent/                 # Agent loop, state, tools, and budgets
│       ├── verifier/              # Finding verifier & line alignment
│       ├── sandbox/               # Subprocess test synthesis and execution
│       ├── memory/                # Team memory, precedents, and vector storage
│       ├── telemetry/             # Tracing, Prometheus metrics, and cost calculation
│       ├── diff.py                # Unified diff parser and hunk indexer
│       └── findings.py            # Pydantic data models for review findings
│
└── tests/                         # Multi-tenant, worker, API, and unit tests
```

## Security

Security guarantees, webhook HMAC verification, untrusted PR content isolation, and multi-tenant scoping are documented in [docs/threat-model.md](docs/threat-model.md).