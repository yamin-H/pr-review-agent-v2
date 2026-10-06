from pydantic import BaseModel, Field

from review.findings import Finding, Severity


class GroundTruthFinding(BaseModel):
    """An annotated expected defect in an evaluation case."""

    file: str = Field(..., description="Target file path relative to repo root")
    line: int = Field(..., ge=1, description="Exact line where the bug occurs")
    description: str = Field(default="", description="Description of the bug")
    severity: Severity = Field(default=Severity.MEDIUM, description="Expected severity")


class EvalCase(BaseModel):
    """A test case representing a PR diff with optional ground-truth defect annotations."""

    id: str = Field(..., description="Unique identifier for this eval case")
    title: str = Field(default="", description="Title of the PR")
    description: str = Field(default="", description="Description of the PR")
    diff: str = Field(..., description="Raw unified diff text")
    ground_truth: list[GroundTruthFinding] = Field(
        default_factory=list,
        description="Annotated defects present in this diff",
    )
    is_clean: bool = Field(
        default=False,
        description="True if this PR has no known defects (used to measure false positive rate)",
    )


class CaseResult(BaseModel):
    """Evaluation metrics for a single PR review run."""

    case_id: str
    is_clean: bool
    reported_count: int
    ground_truth_count: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    line_accuracy: float
    latency_ms: float = 0.0


class EvalSummary(BaseModel):
    """Aggregate benchmark results across an entire evaluation dataset."""

    total_cases: int
    buggy_cases: int
    clean_cases: int
    mean_precision: float
    mean_recall: float
    mean_line_accuracy: float
    clean_false_positive_rate: float
    mean_latency_ms: float
    case_results: list[CaseResult] = Field(default_factory=list)


def evaluate_case(
    case: EvalCase,
    predicted_findings: list[Finding],
    line_tolerance: int = 1,
    latency_ms: float = 0.0,
) -> CaseResult:
    """Evaluate predicted findings against an EvalCase ground truth.

    A predicted finding matches a ground-truth defect if they refer to the same file
    and their line numbers are within `line_tolerance` lines of each other.
    """
    reported_count = len(predicted_findings)
    gt_count = len(case.ground_truth)

    if case.is_clean:
        # For a clean PR, any reported finding is by definition a false positive
        return CaseResult(
            case_id=case.id,
            is_clean=True,
            reported_count=reported_count,
            ground_truth_count=0,
            true_positives=0,
            false_positives=reported_count,
            false_negatives=0,
            precision=1.0 if reported_count == 0 else 0.0,
            recall=1.0,
            line_accuracy=1.0,
            latency_ms=latency_ms,
        )

    matched_gt_indices: set[int] = set()
    exact_line_matches = 0

    for pred in predicted_findings:
        for idx, gt in enumerate(case.ground_truth):
            if idx in matched_gt_indices:
                continue
            if pred.file == gt.file and abs(pred.line - gt.line) <= line_tolerance:
                matched_gt_indices.add(idx)
                if pred.line == gt.line:
                    exact_line_matches += 1
                break

    true_positives = len(matched_gt_indices)
    false_positives = reported_count - true_positives
    false_negatives = gt_count - true_positives

    precision = (true_positives / reported_count) if reported_count > 0 else 0.0
    recall = (true_positives / gt_count) if gt_count > 0 else 1.0
    line_accuracy = (exact_line_matches / true_positives) if true_positives > 0 else 1.0

    return CaseResult(
        case_id=case.id,
        is_clean=False,
        reported_count=reported_count,
        ground_truth_count=gt_count,
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        precision=precision,
        recall=recall,
        line_accuracy=line_accuracy,
        latency_ms=latency_ms,
    )


def summarize_eval_results(case_results: list[CaseResult]) -> EvalSummary:
    """Compute aggregate benchmark metrics across all case evaluation results."""
    total_cases = len(case_results)
    if total_cases == 0:
        return EvalSummary(
            total_cases=0,
            buggy_cases=0,
            clean_cases=0,
            mean_precision=0.0,
            mean_recall=0.0,
            mean_line_accuracy=0.0,
            clean_false_positive_rate=0.0,
            mean_latency_ms=0.0,
            case_results=[],
        )

    clean_results = [r for r in case_results if r.is_clean]
    buggy_results = [r for r in case_results if not r.is_clean]

    clean_cases_with_fp = sum(1 for r in clean_results if r.false_positives > 0)
    clean_fp_rate = (clean_cases_with_fp / len(clean_results)) if clean_results else 0.0

    mean_precision = (
        sum(r.precision for r in buggy_results) / len(buggy_results) if buggy_results else 1.0
    )
    mean_recall = (
        sum(r.recall for r in buggy_results) / len(buggy_results) if buggy_results else 1.0
    )
    mean_line_acc = (
        sum(r.line_accuracy for r in buggy_results) / len(buggy_results) if buggy_results else 1.0
    )
    mean_latency = sum(r.latency_ms for r in case_results) / total_cases

    return EvalSummary(
        total_cases=total_cases,
        buggy_cases=len(buggy_results),
        clean_cases=len(clean_results),
        mean_precision=mean_precision,
        mean_recall=mean_recall,
        mean_line_accuracy=mean_line_acc,
        clean_false_positive_rate=clean_fp_rate,
        mean_latency_ms=mean_latency,
        case_results=case_results,
    )
