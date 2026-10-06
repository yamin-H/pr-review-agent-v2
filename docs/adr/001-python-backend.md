# ADR 001: Unified Python Backend Architecture

**Date:** 2026-10-06  
**Status:** Accepted  

## Context
In version 1 of the review bot, business logic was divided across TypeScript and Python, causing duplicate diff parsing implementations, diverging validation rules, and maintenance overhead.

## Decision
We standardize entirely on Python (3.11+) across all backend components:
- Core review engine and diff parsing
- CLI runner and GitHub webhook worker
- Evaluation harness and SZZ mining scripts

## Consequences
- Single language and shared typing contracts (`Pydantic`, `mypy`).
- Eliminates dual-language logic drift.
- Web layer (Next.js) remains dedicated strictly to frontend presentation.
