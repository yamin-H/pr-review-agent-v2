"""Proof engine that coordinates test synthesis, sandbox execution, and evidence attachment."""

import logging
from pathlib import Path

from review.findings import Finding, Severity
from review.sandbox.models import ExecutionResult
from review.sandbox.runner import SandboxRunner
from review.sandbox.synthesizer import ReproductionSynthesizer
from review.telemetry.otel import SpanKind, get_tracer

logger = logging.getLogger("review.sandbox.prover")


class ProofEngine:
    """Coordinates synthesizing tests and executing them in the sandbox to prove findings."""

    def __init__(
        self,
        synthesizer: ReproductionSynthesizer | None = None,
        runner: SandboxRunner | None = None,
    ) -> None:
        self.synthesizer = synthesizer or ReproductionSynthesizer()
        self.runner = runner or SandboxRunner()

    def attempt_reproduction(
        self,
        finding: Finding,
        diff_text: str,
        repo_root: Path | None = None,
        custom_test_code: str | None = None,
    ) -> tuple[Finding, ExecutionResult]:
        tracer = get_tracer("review.sandbox")
        with tracer.start_as_current_span(
            "sandbox.attempt_reproduction",
            kind=SpanKind.INTERNAL,
            attributes={
                "finding.title": finding.title,
                "finding.file": finding.file,
                "finding.line": finding.line,
                "finding.severity": finding.severity.value,
            },
        ) as otel_span:
            test_code = custom_test_code or self.synthesizer.synthesize_test(
                finding=finding,
                diff_text=diff_text,
            )

            if not test_code.strip():
                logger.warning("No reproduction test synthesized for finding: %s", finding.title)
                return finding, ExecutionResult(
                    error_summary="Failed to synthesize reproduction script.",
                    reproduced=False,
                )

            result = self.runner.run_script(
                script_content=test_code,
                repo_root=repo_root,
            )

            # Update finding with reproduction metadata
            finding.reproduction_code = test_code
            finding.is_reproduced = result.reproduced
            otel_span.set_attribute("reproduced", result.reproduced)

            if result.reproduced:
                evidence_output = (result.stderr or result.stdout).strip()
                # Truncate evidence display if very large
                if len(evidence_output) > 2000:
                    evidence_output = evidence_output[:2000] + "\n...[truncated]"

                finding.reproduction_output = evidence_output
                logger.info("Successfully reproduced defect in sandbox: %s", finding.title)
            else:
                logger.info(
                    "Defect did not trigger expected failure in sandbox: %s (exit code %d)",
                    finding.title,
                    result.exit_code,
                )

            return finding, result

    def prove_findings(
        self,
        findings: list[Finding],
        diff_text: str,
        repo_root: Path | None = None,
        severities_to_test: set[Severity] | None = None,
    ) -> list[Finding]:
        """Iterate over qualifying findings and execute reproduction proofs in sandbox."""
        target_severities = severities_to_test or {Severity.HIGH, Severity.MEDIUM}
        updated_findings: list[Finding] = []

        for f in findings:
            if f.severity in target_severities:
                proven_f, _ = self.attempt_reproduction(
                    finding=f,
                    diff_text=diff_text,
                    repo_root=repo_root,
                )
                updated_findings.append(proven_f)
            else:
                updated_findings.append(f)

        return updated_findings
