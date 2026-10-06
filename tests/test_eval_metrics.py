from evals.metrics import (
    CaseResult,
    EvalCase,
    GroundTruthFinding,
    evaluate_case,
    summarize_eval_results,
)
from review.findings import Finding, Severity


def test_evaluate_case_perfect_match() -> None:
    case = EvalCase(
        id="case-1",
        diff="dummy diff",
        ground_truth=[
            GroundTruthFinding(
                file="app.py",
                line=10,
                description="Zero division",
                severity=Severity.HIGH,
            )
        ],
    )
    predicted = [
        Finding(
            file="app.py",
            line=10,
            title="Zero division",
            body="Check denominator.",
            severity=Severity.HIGH,
        )
    ]

    result = evaluate_case(case, predicted, line_tolerance=1)
    assert result.true_positives == 1
    assert result.false_positives == 0
    assert result.false_negatives == 0
    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.line_accuracy == 1.0


def test_evaluate_case_tolerance_match() -> None:
    case = EvalCase(
        id="case-2",
        diff="dummy diff",
        ground_truth=[
            GroundTruthFinding(
                file="app.py",
                line=10,
                description="Zero division",
                severity=Severity.HIGH,
            )
        ],
    )
    predicted = [
        Finding(
            file="app.py",
            line=11,  # 1 line away
            title="Zero division",
            body="Check denominator.",
            severity=Severity.HIGH,
        )
    ]

    result = evaluate_case(case, predicted, line_tolerance=1)
    assert result.true_positives == 1
    assert result.precision == 1.0
    assert result.recall == 1.0
    # Matched within tolerance, but not exact line
    assert result.line_accuracy == 0.0


def test_evaluate_clean_case_no_findings() -> None:
    case = EvalCase(id="clean-1", diff="clean diff", is_clean=True)
    result = evaluate_case(case, predicted_findings=[])

    assert result.is_clean is True
    assert result.false_positives == 0
    assert result.precision == 1.0


def test_evaluate_clean_case_with_hallucinated_finding() -> None:
    case = EvalCase(id="clean-2", diff="clean diff", is_clean=True)
    predicted = [
        Finding(
            file="app.py",
            line=5,
            title="Fake bug",
            body="Not real.",
            severity=Severity.LOW,
        )
    ]
    result = evaluate_case(case, predicted_findings=predicted)

    assert result.is_clean is True
    assert result.false_positives == 1
    assert result.precision == 0.0


def test_summarize_eval_results_aggregates_correctly() -> None:
    res1 = CaseResult(
        case_id="c1",
        is_clean=False,
        reported_count=1,
        ground_truth_count=1,
        true_positives=1,
        false_positives=0,
        false_negatives=0,
        precision=1.0,
        recall=1.0,
        line_accuracy=1.0,
        latency_ms=100.0,
    )
    res2 = CaseResult(
        case_id="clean1",
        is_clean=True,
        reported_count=1,
        ground_truth_count=0,
        true_positives=0,
        false_positives=1,  # Clean PR got 1 finding -> false positive
        false_negatives=0,
        precision=0.0,
        recall=1.0,
        line_accuracy=1.0,
        latency_ms=50.0,
    )

    summary = summarize_eval_results([res1, res2])
    assert summary.total_cases == 2
    assert summary.buggy_cases == 1
    assert summary.clean_cases == 1
    assert summary.mean_precision == 1.0
    assert summary.clean_false_positive_rate == 1.0
    assert summary.mean_latency_ms == 75.0
