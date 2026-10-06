"""Synthesizes minimal Python reproduction test scripts for candidate bug findings."""

import logging
import re
from typing import Any

from review.findings import Finding
from review.llm import GroqReviewer
from review.security import wrap_untrusted

logger = logging.getLogger("review.sandbox.synthesizer")

SYNTHESIZER_SYSTEM_PROMPT = """\
You are an expert test engineer specializing in reproducing software defects.
Your goal is to write a MINIMAL, SELF-CONTAINED Python reproduction script that proves
a suspected bug.

Guidelines:
1. Self-Contained: Synthesize mock inputs, data structures, or minimal dummy classes if needed
   so the script runs cleanly without external dependencies outside standard library or target repo.
2. Defect Proof: The script must execute the exact code path identified in the finding.
3. Assertive Failure: When the bug is present, the script MUST fail (raise an AssertionError,
   KeyError, TypeError, or other unhandled exception demonstrating the defect).
4. Safety: Do NOT make external network requests, delete files, or run destructive commands.
5. Format: Output ONLY the Python code block enclosed in ```python ... ``` fences.
   No conversational prose.
"""


def extract_python_code(raw_text: str) -> str:
    """Extract Python code block from LLM markdown response."""
    match = re.search(r"```python\s*(.*?)\s*```", raw_text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Fallback to any generic fenced code block
    match_generic = re.search(r"```\s*(.*?)\s*```", raw_text, re.DOTALL)
    if match_generic:
        return match_generic.group(1).strip()

    return raw_text.strip()


class ReproductionSynthesizer:
    """Uses LLM to synthesize minimal reproduction scripts for candidate findings."""

    def __init__(
        self,
        reviewer: GroqReviewer | None = None,
        model: str | None = None,
        llm_client: Any | None = None,
    ) -> None:
        self.reviewer = reviewer or GroqReviewer(model=model)
        self.model = model or self.reviewer.model
        self.llm_client = llm_client

    def synthesize_test(
        self,
        finding: Finding,
        diff_text: str,
        repo_context: str = "",
    ) -> str:
        """Synthesize a standalone Python test script that reproduces the finding."""
        # Testing mock override
        if self.llm_client is not None and hasattr(self.llm_client, "synthesize"):
            return str(self.llm_client.synthesize(finding, diff_text))

        prompt_parts: list[str] = [
            f"Candidate Finding:\n"
            f"File: {finding.file}:{finding.line}\n"
            f"Severity: {finding.severity.value}\n"
            f"Title: {finding.title}\n"
            f"Description: {finding.body}\n",
            f"Diff Context:\n{wrap_untrusted(diff_text, label='diff')}",
        ]

        if repo_context:
            prompt_parts.append(
                f"Repository Context:\n{wrap_untrusted(repo_context, label='repo_context')}"
            )

        prompt_parts.append(
            "Write a minimal standalone Python test script that executes this code and "
            "proves the defect by raising an exception or assertion failure."
        )

        user_prompt = "\n\n".join(prompt_parts)
        client = self.reviewer._ensure_client()

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYNTHESIZER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
            )
            raw_content = response.choices[0].message.content or ""
            return extract_python_code(raw_content)

        except Exception as e:
            logger.error("Failed to synthesize reproduction script: %s", e)
            return ""
