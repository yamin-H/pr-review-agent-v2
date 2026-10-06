# ADR 004: Proof by Execution via Sandboxed Reproduction

**Date:** 2026-10-06  
**Status:** Accepted  

## Context
Code review comments often generate engineer fatigue when they speculate about edge cases that do not actually fail in practice. Static heuristics or unverified LLM reviews can argue convincingly about hypothetical exceptions, but developers routinely dismiss findings that lack reproducible evidence.

Conversely, an automated review that includes a standalone, executable reproduction test with an actual traceback or assertion failure is virtually undeniable.

## Decision
We implement a **Proof by Execution** subsystem (`review.sandbox`) designed to synthesize and execute minimal Python reproduction test scripts for candidate bug findings:

1. **Reproduction Test Synthesis (`ReproductionSynthesizer`):** For candidate defects (especially High/Medium severity), prompt the LLM to generate a minimal, self-contained Python script designed to trigger the defect via assertion failure or unhandled exception.
2. **Ephemeral Sandbox Isolation (`SandboxRunner`):** Execute test scripts within temporary directories under strict constraints:
   - Hard execution timeout (default 10s).
   - Sanitized environment stripping all sensitive secrets and API keys (`GROQ_API_KEY`, tokens, credentials).
   - Captured and bounded stdout/stderr buffers to prevent memory overflows.
3. **Evidence Attaching (`ProofEngine`):**
   - If the script fails as expected (e.g. `AssertionError` or unhandled defect exception), mark `is_reproduced = True` and attach the execution traceback and reproduction code to the finding.
   - If the script passes cleanly or encounters an unrelated syntax/harness error, mark `is_reproduced = False`.
4. **Agent Dynamic Tool (`run_reproduction_test`):** Provide the autonomous review agent with a tool to test its bug hypotheses during its investigation loop.

## Consequences
- Elevates review findings from speculative commentary to verifiable empirical proof.
- Adds an execution step for qualifying findings, bounded by strict timeouts to preserve review responsiveness.
- Sandbox security boundaries ensure untrusted diff scripts cannot exfiltrate host credentials.
