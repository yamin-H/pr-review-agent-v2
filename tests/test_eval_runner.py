import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from evals.metrics import EvalCase, GroundTruthFinding
from evals.run import load_dataset, main, run_benchmark
from review.findings import Finding, ReviewOutput, Severity


def test_load_dataset(tmp_path: Path) -> None:
    case_file = tmp_path / "test_case.json"
    case = EvalCase(
        id="test-1",
        diff="diff text",
        ground_truth=[
            GroundTruthFinding(
                file="app.py",
                line=5,
                description="test",
                severity=Severity.LOW,
            )
        ],
    )
    case_file.write_text(json.dumps(case.model_dump()), encoding="utf-8")

    loaded = load_dataset(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].id == "test-1"
    assert loaded[0].ground_truth[0].line == 5


def test_run_benchmark_with_mock_reviewer() -> None:
    case = EvalCase(
        id="test-case",
        diff=("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,2 @@\n a\n+b\n"),
        ground_truth=[
            GroundTruthFinding(
                file="a.py",
                line=2,
                description="bug",
                severity=Severity.HIGH,
            )
        ],
    )
    mock_reviewer = MagicMock()
    mock_reviewer.review_chunk.return_value = ReviewOutput(
        summary="Found 1 bug",
        findings=[
            Finding(
                file="a.py",
                line=2,
                title="Bug",
                body="Fixed",
                severity=Severity.HIGH,
            )
        ],
    )

    results = run_benchmark([case], reviewer=mock_reviewer)
    assert len(results) == 1
    assert results[0].true_positives == 1
    assert results[0].precision == 1.0
    assert results[0].recall == 1.0


def test_eval_runner_main(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "dataset"
    output_dir = tmp_path / "results"
    dataset_dir.mkdir()

    case = EvalCase(
        id="clean-test",
        diff=(
            "diff --git a/clean.py b/clean.py\n"
            "--- a/clean.py\n"
            "+++ b/clean.py\n"
            "@@ -1,1 +1,2 @@\n"
            " a\n"
            "+b\n"
        ),
        is_clean=True,
    )
    (dataset_dir / "case.json").write_text(json.dumps(case.model_dump()), encoding="utf-8")

    # Run main CLI with temp directories
    exit_code = main(
        [
            "--dataset-dir",
            str(dataset_dir),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == 0
    saved_files = list(output_dir.glob("baseline_*.json"))
    assert len(saved_files) == 1


def test_eval_runner_main_with_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dataset_dir = tmp_path / "dataset"
    output_dir = tmp_path / "results"
    dataset_dir.mkdir()

    case = EvalCase(
        id="clean-test",
        diff=(
            "diff --git a/clean.py b/clean.py\n"
            "--- a/clean.py\n"
            "+++ b/clean.py\n"
            "@@ -1,1 +1,2 @@\n"
            " a\n"
            "+b\n"
        ),
        is_clean=True,
    )
    (dataset_dir / "case.json").write_text(json.dumps(case.model_dump()), encoding="utf-8")

    mock_runner = MagicMock()
    mock_runner.review_to_output.return_value = ReviewOutput(summary="Done", findings=[])
    monkeypatch.setattr("evals.run.AgentRunner", lambda **kwargs: mock_runner)

    exit_code = main(
        [
            "--dataset-dir",
            str(dataset_dir),
            "--output-dir",
            str(output_dir),
            "--agent",
        ]
    )
    assert exit_code == 0
    saved_files = list(output_dir.glob("agent_*.json"))
    assert len(saved_files) == 1
