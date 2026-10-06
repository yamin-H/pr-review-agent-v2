# ADR 002: Ground-Truth Evaluation via SZZ and Clean PRs

**Date:** 2026-10-06  
**Status:** Accepted  

## Context
Many AI review tools evaluate against subjective human comments or fabricated synthetic bugs. This leads to ungrounded claims, ignores false positives on clean code, and fails to measure actual defect detection.

## Decision
We evaluate the review agent using an empirical, two-pronged methodology:
1. **SZZ Algorithm for Defect Detection:** Trace verified bug-fixing commits back to the exact bug-introducing commit (BIC) via `git blame`. The BIC's diff serves as the ground truth.
2. **Clean PR Evaluation for False Positive Control:** Evaluate against merged pull requests with no reported defects to measure false positive penalties directly.
3. **Automated Benchmark Runner:** A single executable script (`evals/run.py`) runs the agent, scores findings against ground truth, and writes immutable benchmark logs to `evals/results/`.

## Consequences
- Every metric published in the project README is reproducible and backed by saved benchmark logs.
- Prevents premature optimization of features that do not measurably improve precision or recall.
- Acknowledges known threats to validity (e.g., potential model contamination on public historical repositories).
