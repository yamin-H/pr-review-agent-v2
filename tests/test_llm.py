from unittest.mock import MagicMock

import pytest

from review.findings import Severity
from review.llm import GroqReviewer, LLMReviewError


def test_groq_reviewer_successful_parse() -> None:
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = (
        '{"summary": "Changes look solid with minor edge case.", '
        '"findings": [{"file": "app.py", "line": 13, "title": "Check None", '
        '"body": "Value might be None.", "severity": "high"}]}'
    )
    mock_response = MagicMock(choices=[mock_choice])
    mock_client.chat.completions.create.return_value = mock_response

    reviewer = GroqReviewer(api_key="test_key", client=mock_client)
    result = reviewer.review_chunk("diff content")

    assert result.summary == "Changes look solid with minor edge case."
    assert len(result.findings) == 1
    assert result.findings[0].file == "app.py"
    assert result.findings[0].line == 13
    assert result.findings[0].severity == Severity.HIGH


def test_groq_reviewer_retries_on_malformed_json_and_succeeds() -> None:
    mock_client = MagicMock()
    # First response is invalid JSON, second response is valid JSON
    bad_choice = MagicMock()
    bad_choice.message.content = "Invalid non-JSON output"
    bad_response = MagicMock(choices=[bad_choice])

    good_choice = MagicMock()
    good_choice.message.content = '{"summary": "Recovered.", "findings": []}'
    good_response = MagicMock(choices=[good_choice])

    mock_client.chat.completions.create.side_effect = [bad_response, good_response]

    reviewer = GroqReviewer(api_key="test_key", client=mock_client)
    result = reviewer.review_chunk("diff content", max_retries=2, backoff_seconds=0.01)

    assert result.summary == "Recovered."
    assert len(result.findings) == 0
    assert mock_client.chat.completions.create.call_count == 2


def test_groq_reviewer_raises_after_exceeding_max_retries() -> None:
    mock_client = MagicMock()
    bad_choice = MagicMock()
    bad_choice.message.content = "Still broken JSON"
    mock_client.chat.completions.create.return_value = MagicMock(choices=[bad_choice])

    reviewer = GroqReviewer(api_key="test_key", client=mock_client)
    with pytest.raises(LLMReviewError):
        reviewer.review_chunk("diff content", max_retries=2, backoff_seconds=0.01)


def test_groq_reviewer_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    reviewer = GroqReviewer(api_key=None, client=None)
    reviewer.client = None
    with pytest.raises(LLMReviewError, match="GROQ_API_KEY is not set"):
        reviewer.review_chunk("diff")

