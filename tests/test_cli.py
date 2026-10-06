from pathlib import Path
from unittest.mock import MagicMock

from review.cli import main, review_diff
from review.findings import Finding, ReviewOutput, Severity

SAMPLE_DIFF = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -10,3 +12,4 @@
 context line
-old line
+new line
+another new line
 context line
"""


def test_review_diff_end_to_end_with_mock_reviewer() -> None:
    mock_reviewer = MagicMock()
    mock_reviewer.review_chunk.return_value = ReviewOutput(
        summary="Found 1 valid issue and 1 hallucination.",
        findings=[
            Finding(
                file="app.py",
                line=13,  # Valid line
                title="Potential flaw",
                body="Check boundaries.",
                severity=Severity.HIGH,
            ),
            Finding(
                file="app.py",
                line=999,  # Hallucinated line
                title="Ghost line",
                body="Not in diff.",
                severity=Severity.LOW,
            ),
        ],
    )

    result = review_diff(SAMPLE_DIFF, reviewer=mock_reviewer)
    assert result.summary == "Found 1 valid issue and 1 hallucination."
    # The hallucinated line 999 must be dropped by validation
    assert len(result.findings) == 1
    assert result.findings[0].line == 13


def test_review_diff_empty_or_ignored() -> None:
    empty_diff = "diff --git a/package-lock.json b/package-lock.json\n"
    result = review_diff(empty_diff)
    assert "No reviewable changes found" in result.summary
    assert len(result.findings) == 0


def test_cli_main_with_file_argument(tmp_path: Path) -> None:
    diff_file = tmp_path / "sample.diff"
    diff_file.write_text(SAMPLE_DIFF, encoding="utf-8")

    # Run main with --json and file path
    # Since GroqReviewer requires an API key, we pass an empty diff or verify arg parsing
    empty_patch = tmp_path / "empty.diff"
    empty_patch.write_text("", encoding="utf-8")

    code = main([str(empty_patch), "--json"])
    assert code == 0
