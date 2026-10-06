# ADR 003: Finding Verification Strategy

**Date:** 2026-10-06  
**Status:** Accepted  

## Context
Raw LLM reviews (whether single-shot or autonomous agent loops) suffer from high sensitivity. In pursuit of detecting subtle bugs, models frequently report speculative issues, cosmetic style nitpicks, or misunderstandings of parent framework context that would be caught upstream. This dilutes engineer trust and inflates false positive rates.

Standard multi-agent or self-correction architectures often suffer from confirmation bias: asking an agent "Is your previous finding correct?" typically yields an agreeable "Yes".

## Decision
We introduce an explicit, adversarial verification stage (`FindingVerifier`) that operates with a Devil's Advocate refutation objective:
1. **Adversarial Refutation Framing:** The verifier prompt explicitly instructs the LLM to assume the finding is a false positive or harmless edge case, demanding undeniable evidence to justify retaining it.
2. **Three-Way Verdict Categorization:**
   - `KEEP`: The candidate finding survived adversarial scrutiny; it indicates a severe, verifiable defect.
   - `DROP`: The finding was disproven, speculative, or a harmless stylistic preference.
   - `REVISE`: The defect is legitimate, but the title, explanation, line grounding, or severity needed calibration.
3. **Deterministic Diff Line Enforcement:** If a revised finding alters the reported line number, it is verified against the diff's added-line index; if invalid, it falls back to the original verified line.
4. **Dual Execution Modes:**
   - **Autonomous Tool (`verify_finding`):** The agent can proactively invoke verification on individual findings during its investigation loop.
   - **Pipeline Filter (`--verify`):** Deterministic post-processing pass executed prior to comment publication.
5. **Fail-Safe Graceful Degradation:** In the event of transient network errors or unrecoverable JSON parse failures, the pipeline logs a warning and retains candidate findings rather than abruptly halting the review.

## Consequences
- False positives on clean code and subtle non-bugs are dramatically reduced.
- Slightly increases review latency and token usage by adding an evaluation pass over proposed findings.
- Enables rigorous ablation benchmarking (`Single-Shot`, `Single-Shot + Verifier`, `Agent`, `Agent + Verifier`) on the empirical SZZ dataset.
