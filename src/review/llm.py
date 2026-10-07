import json
import logging
import os
import time
from typing import Any

from dotenv import load_dotenv
from groq import Groq, RateLimitError
from pydantic import ValidationError

from review.findings import ReviewOutput
from review.prompts import REVIEWER_SYSTEM_PROMPT, format_review_prompt
from review.telemetry.otel import SpanKind, get_tracer

load_dotenv()
logger = logging.getLogger(__name__)

DEFAULT_MODEL = "openai/gpt-oss-120b"


class LLMReviewError(Exception):
    """Raised when the LLM review fails after all retries."""


class GroqReviewer:
    """LLM client for conducting code reviews using Groq with structured outputs and retries."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        self.model = model or os.getenv("GROQ_MODEL", DEFAULT_MODEL)

        if client is not None:
            self.client = client
        elif self.api_key:
            self.client = Groq(api_key=self.api_key)
        else:
            self.client = None

    def _ensure_client(self) -> Any:
        if self.client is None:
            raise LLMReviewError(
                "GROQ_API_KEY is not set. Please provide it via environment variable or .env file."
            )
        return self.client

    def review_chunk(
        self,
        diff_chunk_text: str,
        pr_title: str | None = None,
        pr_description: str | None = None,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
    ) -> ReviewOutput:
        """Call Groq to review a formatted diff chunk and return structured ReviewOutput."""
        client = self._ensure_client()
        user_prompt = format_review_prompt(
            diff_content=diff_chunk_text,
            pr_title=pr_title,
            pr_description=pr_description,
        )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": REVIEWER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        last_error: Exception | None = None
        tracer = get_tracer("review.llm")
        with tracer.start_as_current_span(
            "llm.groq.review_chunk",
            kind=SpanKind.CLIENT,
            attributes={"model": self.model, "pr_title": pr_title or ""},
        ) as otel_span:
            for attempt in range(1, max_retries + 1):
                try:
                    response = client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        response_format={"type": "json_object"},
                        temperature=0.1,
                    )

                    content = response.choices[0].message.content or "{}"
                    data = json.loads(content)
                    output = ReviewOutput.model_validate(data)
                    otel_span.set_attribute("findings_count", len(output.findings))
                    if getattr(response, "usage", None):
                        usage = response.usage
                        otel_span.set_attribute(
                            "prompt_tokens", getattr(usage, "prompt_tokens", 0)
                        )
                        otel_span.set_attribute(
                            "completion_tokens", getattr(usage, "completion_tokens", 0)
                        )
                    return output

                except RateLimitError as e:
                    last_error = e
                    wait_time = backoff_seconds * (2 ** (attempt - 1))
                    logger.warning(
                        "Groq rate limit encountered on attempt %d/%d. Waiting %.1fs: %s",
                        attempt,
                        max_retries,
                        wait_time,
                        e,
                    )
                    time.sleep(wait_time)

                except (json.JSONDecodeError, ValidationError) as e:
                    last_error = e
                    logger.warning(
                        "Invalid structured output on attempt %d/%d: %s",
                        attempt,
                        max_retries,
                        e,
                    )
                    # Feed the validation failure back to the model for correction
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"Your previous response had schema validation errors: {e}. "
                                "Please fix them and return valid JSON adhering strictly to: "
                                '{"summary": "...", "findings": [{"file": "...", "line": 1, '
                                '"title": "...", "body": "...", "severity": "medium"}]}'
                            ),
                        }
                    )
                    time.sleep(backoff_seconds)

                except Exception as e:
                    last_error = e
                    logger.error("Unexpected error during LLM call on attempt %d: %s", attempt, e)
                    time.sleep(backoff_seconds)

            raise LLMReviewError(
                f"Failed to generate valid review after {max_retries} attempts: {last_error}"
            ) from last_error
