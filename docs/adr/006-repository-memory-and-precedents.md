# ADR 006: Repository Memory and Historical Precedents

**Date:** 2026-10-07  
**Status:** Accepted  

## Context
Code reviews conducted in isolation often miss recurring bugs, architecture conventions, and hard-learned regressions unique to a specific codebase:
1. **Recurring Regressions:** Teams frequently encounter recurring vulnerability patterns (e.g., forgotten header sanitization, unhandled JSON decode exceptions, race conditions).
2. **Review Grounding & Authority:** Finding comments that lack concrete citations are often perceived as speculative. Reviewers accept feedback much faster when cited with historical precedents (e.g. past PRs or CVEs).
3. **Strict Multi-Tenant Isolation:** When serving multiple organizations or repositories, knowledge and precedents from one repository must NEVER leak or influence reviews in another repository.
4. **Agent Actionability:** The autonomous review agent must be equipped with an intentional tool (`search_precedents`) allowing it to actively query historical knowledge during its plan-act-observe cycle.

## Decision
We implement a lightweight, persistent, and tenant-isolated repository memory architecture (`src/review/memory`):

1. **Persistent SQLite Store (`MemoryStore`):** Precedents are stored in SQLite (`.reviewer_memory.db`) with indexes on repository identifiers. In-memory mode (`:memory:`) is supported seamlessly for isolated test suites.
2. **Strict Repository Tenant Scoping:** Every memory write and query is strictly partitioned by `repo` (normalized lowercase `owner/repo`). Queries for `pallets/flask` never match entries for `psf/requests` regardless of keyword overlap.
3. **Lexical Overlap Scoring (`_compute_relevance_score`):** Implements weighted token relevance scoring where title matches are weighted highest (3.0x), followed by description (1.5x) and code snippets (1.0x), with normalized asymptotic scoring in $[0.0, 1.0]$.
4. **File Path Pattern Matching:** Precedents support glob patterns (`file_pattern`) so that file-specific rules (e.g., `src/auth/*.py`) only activate when reviewing relevant files.
5. **Historical Precedent Ingestion (`PrecedentMiner`):** Automatically mines bug fixes, CVE resolutions, and PR references from git commit histories and SZZ datasets, generating structured precedents with verifiable citations.
6. **Agent Memory Tool (`search_precedents`):** Registered in `TOOL_DEFINITIONS` alongside read-only code tools, giving the agent on-demand access to team knowledge during review planning.
7. **Grounded Inline Citations:** The publisher embeds cited precedents directly into inline GitHub comments (`📚 Repository Precedent Cited: [PR #412] ...`).

## Consequences
- Elevates review quality by catching recurring repository-specific patterns and regressions.
- Provides verifiable citations for flagged defects, improving developer trust and acceptance.
- Guarantees zero cross-repository data contamination across multi-tenant deployments.
- Minimal operational footprint requiring no external vector database infrastructure.
