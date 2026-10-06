# Autonomous PR Review Agent: Scope & Boundaries

**Document Status:** Approved  
**Version:** 2.0  
**Phase:** Phase 0 (Setup)  

---

## 1. Executive Summary

The **Autonomous PR Review Agent** is a GitHub App designed to inspect pull requests (PRs) autonomously, gather necessary repository context via read-only tools, verify candidate findings against execution sandboxes and repo precedents, and post precision inline comments on exact diff lines.

Unlike naive single-prompt review bots, this system emphasizes **verifiable evidence over speculative commentary**. It operates with strict security boundaries, never approves or blocks pull requests, and holds human engineers in final control.

To avoid the architectural pitfalls of broad-but-shallow prototypes, this document establishes the explicit boundary between **what is actively built in v1 (MVP)** and **what is deliberately excluded (Non-Goals)**.

---

## 2. In-Scope for v1 (Active Implementation)

### 2.1 Core Agentic Engine
* **Dynamic Autonomy Loop:** A plan–act–observe cycle governed by budget thresholds (maximum steps, token usage, dollar cost) and loop-detection heuristics.
* **Deterministic Line Grounding:** Extraction and validation of diff line numbers using absolute new-file line mapping (`src/review/diff.py`).
* **Untrusted Content Wrapping:** Strict data demarcation separating prompt instructions from untrusted user content (diffs, commit messages, PR descriptions).
* **Automated File Filtering:** Ignoring high-noise files including lockfiles, minified assets, vendored directories, and compiled binaries (`src/review/filters.py`).
* **Finding Self-Verification:** A discrete verification step designed to attempt disproving candidate findings prior to publication.

### 2.2 Proof by Execution (Standout Differentiator)
* **Sandboxed Test Reproduction:** For high-severity bug findings in Python, the agent attempts to synthesize a minimal failing reproduction script/test (`pytest`).
* **Ephemeral Isolated Execution:** Reproduction tests execute in resource-constrained, non-root, network-isolated container sandboxes.
* **Evidence-Backed Findings:** Reviews cite reproduction output or drop unprovable speculative defects.

### 2.3 Evaluation & Empirical Rigor
* **SZZ Bug Dataset:** An evaluation suite based on real historical bug-introducing commits and their corresponding fixes to measure recall and precision quantitatively.
* **Ablation Testing:** Clear measurements of performance with and without individual tools (call graphs, verifier, memory).
* **Calibrated Silence:** Suppressing low-confidence findings on repositories with high false-positive histories.

### 2.4 Repository Memory & Precedents
* **Precedent Mining:** Ingestion of historical review discussions, reverted commits, and bug-fix patterns to cite precedents (e.g., *"this pattern was reverted in PR #412"*).
* **Vector Memory:** Organization-scoped vector storage (`pgvector`) storing code snippets, resolved findings, and review outcomes.

### 2.5 Security & Architecture
* **Tenant Isolation:** Multi-tenancy enforced at the database layer via PostgreSQL Row-Level Security (RLS) and organization scoping on every query.
* **Webhook Hardening:** Constant-time HMAC signature verification, replay protection using cached delivery IDs, and base-branch configuration retrieval (`.reviewer.yml`).
* **Service Decomposition:**
  * **API Layer:** FastAPI webhook receiver and REST management endpoints.
  * **Queue Worker:** Asynchronous background job worker using Redis and `arq`.
  * **Observability:** Structured tracing with Langfuse and OpenTelemetry.

---

## 3. Explicit Non-Goals (Out of Scope for v1)

Deliberately omitted capabilities, with architectural rationale:

| Category | Out of Scope Item | Rationale & Trade-off |
| :--- | :--- | :--- |
| **Workflow Governance** | **Approving or Blocking Merges** | The agent strictly issues `COMMENT` reviews. Humans retain absolute merge governance; bots should advise rather than gatekeep. |
| **Code Remediation** | **Automated Fix PRs / Commits** | Generating fix PRs introduces high liability and review fatigue. v1 focuses on diagnostic accuracy and proof rather than automated synthesis. |
| **Commercial Rails** | **Billing, Subscriptions & Quotas** | Payment gateways, metering, and invoicing add accidental complexity unrelated to core agentic capability. |
| **User Administration** | **Multi-Seat RBAC & Team Management** | Enterprise permissions (roles, groups, SSO) are deferred in favor of direct GitHub App organization-level authorization. |
| **Language Breadth** | **Universal Language Support** | Deep support for two ecosystems (Python first with execution sandboxing, followed by TypeScript) is prioritized over shallow parsing across dozens of languages. |
| **Product Surface** | **Marketing Landing Pages & Visual Fluff** | The web layer is limited to an operational Next.js dashboard displaying review traces, cost metrics, and repository settings. |
| **Infrastructure** | **Multi-Region Distributed Database** | A single managed PostgreSQL instance with connection pooling meets all performance targets without distributed consensus overhead. |

---

## 4. Production Roadmap (Documented Future State)

Features omitted from v1 are documented here with their prospective target architectures:

1. **Firecracker / gVisor MicroVM Sandboxing:** Transitioning from Docker containers to hardware-virtualized microVMs for executing untrusted reproduction scripts with sub-millisecond boot times and stronger isolation guarantees.
2. **Automated Remediation Branches:** Allowing developers to invoke `/agent fix` on an existing comment thread, triggering a verified diff generation branch validated against CI.
3. **Multi-Tenant Enterprise RBAC & SAML/SSO:** Integration with Okta/Azure AD and fine-grained repository permission boundaries.
4. **Multi-Language Execution Sandboxes:** Extending reproduction proof harnesses to TypeScript (Jest/Vitest), Go, and Rust.
5. **Streaming Inline Reviews:** Leveraging GitHub review draft APIs to progressively publish findings as individual agent sub-tasks complete.
