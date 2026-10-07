"""Adversarial verification pass to challenge and disprove candidate review findings."""

import json
import logging
import time
from enum import StrEnum
from typing import Any

from groq import RateLimitError
from pydantic import BaseModel, Field, ValidationError

from review.findings import Finding, Severity
from review.llm import GroqReviewer
from review.security import wrap_untrusted
from review.telemetry.otel import SpanKind, get_tracer
from review.validate import validate_findings_against_diff

logger = logging.getLogger("review.verifier")


class VerificationDecision(StrEnum):
    KEEP = "keep"
    DROP = "drop"
    REVISE = "revise"


class VerificationResult(BaseModel):
    finding_index: int = Field(default=0, description="0-indexed position of the finding")
    decision: VerificationDecision = Field(
        ..., description="Action to take: 'keep', 'drop', or 'revise'"
    )
    rationale: str = Field(..., description="Adversarial reasoning justifying the decision")
    revised_title: str | None = Field(default=None, description="Updated title if revising")
    revised_body: str | None = Field(default=None, description="Updated explanation if revising")
    revised_severity: Severity | None = Field(
        default=None, description="Updated severity if revising"
    )
    revised_line: int | None = Field(default=None, description="Updated line number if revising")


class BatchVerificationResponse(BaseModel):
    results: list[VerificationResult] = Field(
        default_factory=list, description="Verification verdict for each evaluated finding"
    )


VERIFIER_SYSTEM_PROMPT = """\
You are a senior principal staff engineer serving as an adversarial verification filter.
Your task is to aggressively CHALLENGE and DISPROVE candidate code review findings to eliminate
false positives.

Automated reviewers frequently hallucinate, misread surrounding context, or flag harmless stylistic
preferences.
Your mindset: Assume each finding is a FALSE POSITIVE until undeniable evidence proves it is a
genuine defect.

Evaluation Checklist:
1. False Positive Challenge: Could this code actually be correct or safe? Is the input sanitized
   upstream, or an exception caught by a parent caller?
2. Genuine Bug vs. Nitpick: Is this a critical bug, logic flaw, or security defect? Or is it a
   trivial nitpick, cosmetic preference, or minor typing suggestion? If not a bug, DROP it.
3. Grounding & Scope: Does the finding accurately criticize the newly added code, or does it blame
   pre-existing code merely adjacent to the diff?
4. Calibration: Is the severity realistic, or exaggerated?

For each candidate finding, produce a decision:
- "keep": The finding points to an indisputable, severe bug or security vulnerability that survives
  all counter-arguments.
- "drop": The finding is incorrect, speculative, harmless, a minor stylistic nitpick, or a false
  positive.
- "revise": The defect is real, but the explanation, line number, or severity needs adjustment.

You must return valid JSON matching the schema:
{
  "results": [
    {
      "finding_index": 0,
      "decision": "keep" | "drop" | "revise",
      "rationale": "...",
      "revised_title": null,
      "revised_body": null,
      "revised_severity": null,
      "revised_line": null
    }
  ]
}
"""


def format_verification_prompt(
    diff_text: str,
    findings: list[Finding],
    pr_title: str = "",
    pr_description: str = "",
) -> str:
    """Format user prompt presenting the diff and all candidate findings for verification."""
    sections: list[str] = []
    if pr_title or pr_description:
        meta = f"Title: {pr_title or 'Untitled'}\nDescription: {pr_description or 'None'}"
        sections.append("Pull Request Metadata:\n" + wrap_untrusted(meta, label="metadata"))

    sections.append(
        "Unified Diff (with line numbers prefixed to added code):\n"
        + wrap_untrusted(diff_text, label="diff")
    )

    findings_text: list[str] = []
    for i, f in enumerate(findings):
        findings_text.append(
            f"Finding #{i}:\n"
            f"  File: {f.file}\n"
            f"  Line: {f.line}\n"
            f"  Severity: {f.severity.value}\n"
            f"  Title: {f.title}\n"
            f"  Body: {f.body}\n"
        )
    sections.append(
        "Candidate Findings to Verify:\n"
        + wrap_untrusted("\n".join(findings_text), label="findings")
    )

    sections.append(
        "Act as the devil's advocate. For EACH candidate finding above (Finding #0 to Finding #"
        f"{len(findings) - 1}), evaluate if it is a false positive or harmless. "
        "Decide 'keep', 'drop', or 'revise', and provide rigorous rationale."
    )
    return "\n\n".join(sections)


class FindingVerifier:
    """Independent verification pass designed to challenge and disprove candidate findings."""

    def __init__(
        self,
        reviewer: GroqReviewer | None = None,
        model: str | None = None,
        llm_client: Any | None = None,
    ) -> None:
        self.reviewer = reviewer or GroqReviewer(model=model)
        self.model = model or self.reviewer.model
        self.llm_client = llm_client

    def _parse_verification_json(self, raw_data: Any) -> list[VerificationResult]:
        """Normalize JSON response into a list of VerificationResult items."""
        if isinstance(raw_data, list):
            return [VerificationResult.model_validate(item) for item in raw_data]
        if isinstance(raw_data, dict):
            if "results" in raw_data and isinstance(raw_data["results"], list):
                return [VerificationResult.model_validate(item) for item in raw_data["results"]]
            if "decision" in raw_data:
                return [VerificationResult.model_validate(raw_data)]
        return []

    def verify_findings(
        self,
        findings: list[Finding],
        diff_text: str,
        pr_title: str = "",
        pr_description: str = "",
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
    ) -> tuple[list[Finding], list[VerificationResult]]:
        """Adversarially evaluate a list of findings, returning (surviving_findings, verdicts)."""
        tracer = get_tracer("review.verifier")
        with tracer.start_as_current_span(
            "verifier.adversarial_challenge",
            kind=SpanKind.INTERNAL,
            attributes={"candidate_findings_count": len(findings), "model": self.model},
        ) as otel_span:
            surviving, verdicts = self._verify_findings_internal(
                findings=findings,
                diff_text=diff_text,
                pr_title=pr_title,
                pr_description=pr_description,
                max_retries=max_retries,
                backoff_seconds=backoff_seconds,
            )
            otel_span.set_attribute("surviving_findings_count", len(surviving))
            otel_span.set_attribute(
                "dropped_findings_count", len(findings) - len(surviving)
            )
            return surviving, verdicts

    def _verify_findings_internal(
        self,
        findings: list[Finding],
        diff_text: str,
        pr_title: str = "",
        pr_description: str = "",
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
    ) -> tuple[list[Finding], list[VerificationResult]]:
        """Internal adversarial evaluation implementation."""
        if not findings:
            return [], []

        # Deterministic mock override for testing
        if self.llm_client is not None and hasattr(self.llm_client, "verify"):
            mock_res: tuple[list[Finding], list[VerificationResult]] = self.llm_client.verify(
                findings, diff_text
            )
            return mock_res

        client = self.reviewer._ensure_client()
        user_prompt = format_verification_prompt(
            diff_text=diff_text,
            findings=findings,
            pr_title=pr_title,
            pr_description=pr_description,
        )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": VERIFIER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        parsed_results: list[VerificationResult] = []

        for attempt in range(1, max_retries + 1):
            try:
                response = client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=0.1,
                )
                content = response.choices[0].message.content or "{}"
                raw_data = json.loads(content)
                parsed_results = self._parse_verification_json(raw_data)
                break

            except RateLimitError as e:
                wait_time = backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "Verifier rate limit on attempt %d/%d. Waiting %.1fs: %s",
                    attempt,
                    max_retries,
                    wait_time,
                    e,
                )
                time.sleep(wait_time)

            except (json.JSONDecodeError, ValidationError) as e:
                logger.warning(
                    "Invalid structured verification output on attempt %d/%d: %s",
                    attempt,
                    max_retries,
                    e,
                )
                if attempt < max_retries:
                    error_feedback = (
                        f"Your previous response failed validation: {e}. "
                        "Please return valid JSON with a 'results' list of "
                        "{finding_index, decision, rationale}."
                    )
                    messages.append({"role": "user", "content": error_feedback})
            except Exception as e:
                logger.error("Unexpected error in FindingVerifier: %s", e)
                break

        # Map results by finding index; fallback by position if needed
        results_by_index: dict[int, VerificationResult] = {}
        for idx, res in enumerate(parsed_results):
            target_idx = res.finding_index if 0 <= res.finding_index < len(findings) else idx
            results_by_index[target_idx] = res

        surviving: list[Finding] = []
        all_verdicts: list[VerificationResult] = []

        for i, original in enumerate(findings):
            verdict = results_by_index.get(i)
            if verdict is None:
                # Default to KEEP if no explicit verdict returned
                verdict = VerificationResult(
                    finding_index=i,
                    decision=VerificationDecision.KEEP,
                    rationale="Retained by default (no explicit verifier objection).",
                )
            all_verdicts.append(verdict)

            if verdict.decision == VerificationDecision.DROP:
                logger.info(
                    "Verifier dropped finding #%d [%s:%d]: %s. Rationale: %s",
                    i,
                    original.file,
                    original.line,
                    original.title,
                    verdict.rationale,
                )
                continue

            elif verdict.decision == VerificationDecision.REVISE:
                revised_candidate = Finding(
                    file=original.file,
                    line=verdict.revised_line or original.line,
                    title=verdict.revised_title or original.title,
                    body=verdict.revised_body or original.body,
                    severity=verdict.revised_severity or original.severity,
                )
                # Verify that revised line exists in diff added lines
                valid, _ = validate_findings_against_diff([revised_candidate], diff_text)
                if valid:
                    surviving.append(revised_candidate)
                    logger.info("Verifier revised finding #%d: %s", i, revised_candidate.title)
                else:
                    # Fallback to original line if revised line was invalid
                    revised_candidate.line = original.line
                    surviving.append(revised_candidate)
                    logger.info(
                        "Verifier revised finding #%d (retained original line): %s",
                        i,
                        revised_candidate.title,
                    )

            else:  # KEEP
                surviving.append(original)

        return surviving, all_verdicts

    def verify_single(
        self,
        finding: Finding,
        diff_text: str,
        pr_title: str = "",
        pr_description: str = "",
    ) -> VerificationResult:
        """Adversarially evaluate a single candidate finding."""
        _, verdicts = self.verify_findings(
            findings=[finding],
            diff_text=diff_text,
            pr_title=pr_title,
            pr_description=pr_description,
        )
        if verdicts:
            return verdicts[0]
        return VerificationResult(
            finding_index=0,
            decision=VerificationDecision.KEEP,
            rationale="Retained by default.",
        )
