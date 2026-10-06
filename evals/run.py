"""Benchmark runner for evaluating the Autonomous PR Review Agent.

Runs the review agent against real labeled pull request diffs and SZZ datasets,
computes empirical precision, recall, line accuracy, and false positive rates,
and persists benchmark results to disk.
"""

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from evals.metrics import CaseResult, EvalCase, evaluate_case, summarize_eval_results
from review.cli import review_diff
from review.llm import GroqReviewer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("evals.runner")


def load_dataset(dataset_dir: Path) -> list[EvalCase]:
    """Load all EvalCase JSON definitions recursively from a directory."""
    cases: list[EvalCase] = []
    if not dataset_dir.exists():
        return cases

    for path in sorted(dataset_dir.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            case = EvalCase.model_validate(data)
            cases.append(case)
        except Exception as e:
            logger.warning("Failed to parse evaluation case %s: %s", path.name, e)

    return cases


def run_benchmark(
    cases: list[EvalCase],
    reviewer: GroqReviewer | None = None,
    line_tolerance: int = 1,
) -> list[CaseResult]:
    """Execute the review pipeline across evaluation cases and measure results."""
    if reviewer is None:
        reviewer = GroqReviewer()

    results: list[CaseResult] = []

    for i, case in enumerate(cases, 1):
        logger.info(
            "[%d/%d] Evaluating %s (%s, clean=%s)",
            i,
            len(cases),
            case.id,
            case.title or "Untitled",
            case.is_clean,
        )

        start = time.perf_counter()
        try:
            review_output = review_diff(case.diff, reviewer=reviewer)
        except Exception as e:
            logger.error("Review execution error on case %s: %s", case.id, e)
            continue
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        case_res = evaluate_case(
            case=case,
            predicted_findings=review_output.findings,
            line_tolerance=line_tolerance,
            latency_ms=elapsed_ms,
        )
        results.append(case_res)

        logger.info(
            "  -> TP: %d, FP: %d, FN: %d, Precision: %.1f%%, Recall: %.1f%%, Latency: %.0fms",
            case_res.true_positives,
            case_res.false_positives,
            case_res.false_negatives,
            case_res.precision * 100.0,
            case_res.recall * 100.0,
            case_res.latency_ms,
        )

    return results


def print_benchmark_table(summary_dict: dict[str, object], model_name: str) -> None:
    """Print clean benchmark results table to standard output."""
    total = summary_dict.get("total_cases", 0)
    buggy = summary_dict.get("buggy_cases", 0)
    clean = summary_dict.get("clean_cases", 0)
    prec = float(summary_dict.get("mean_precision", 0.0)) * 100.0  # type: ignore[arg-type]
    rec = float(summary_dict.get("mean_recall", 0.0)) * 100.0  # type: ignore[arg-type]
    line_acc = float(summary_dict.get("mean_line_accuracy", 0.0)) * 100.0  # type: ignore[arg-type]
    fp_rate = float(summary_dict.get("clean_false_positive_rate", 0.0)) * 100.0  # type: ignore[arg-type]
    lat = float(summary_dict.get("mean_latency_ms", 0.0))  # type: ignore[arg-type]

    print("\n" + "=" * 70)
    print("AUTONOMOUS PR REVIEW AGENT — EVALUATION BENCHMARK RESULTS")
    print("=" * 70)
    print(f"Model:                    {model_name}")
    print(f"Total Evaluated Cases:    {total}")
    print(f"  - Bug-introducing cases:{buggy}")
    print(f"  - Clean PR cases:       {clean}")
    print("-" * 70)
    print(f"Precision:                {prec:.1f}%")
    print(f"Recall:                   {rec:.1f}%")
    print(f"Line Placement Accuracy:  {line_acc:.1f}%")
    print(f"Clean PR False Positive:  {fp_rate:.1f}%")
    print(f"Mean Latency:             {lat:.0f} ms")
    print("=" * 70 + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run evaluation benchmarks against the PR dataset")
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=Path("evals/dataset"),
        help="Root directory containing evaluation case JSON files",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("evals/results"),
        help="Directory where evaluation results JSON will be saved",
    )
    parser.add_argument("--model", help="Groq model override (e.g. llama-3.3-70b-versatile)")
    parser.add_argument(
        "--tolerance",
        type=int,
        default=1,
        help="Allowed line tolerance for matching findings to ground truth",
    )
    parser.add_argument("--limit", type=int, help="Limit number of cases to evaluate")

    args = parser.parse_args(argv)

    cases = load_dataset(args.dataset_dir)
    if not cases:
        print(f"Error: No evaluation cases found in {args.dataset_dir}", file=sys.stderr)
        return 1

    if args.limit:
        cases = cases[: args.limit]

    reviewer = GroqReviewer(model=args.model)
    case_results = run_benchmark(cases, reviewer=reviewer, line_tolerance=args.tolerance)

    summary = summarize_eval_results(case_results)
    summary_dict = summary.model_dump()

    print_benchmark_table(summary_dict, model_name=reviewer.model or "unknown")

    # Persist benchmark result to file
    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out_file = args.output_dir / f"baseline_{timestamp}.json"

    result_payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "model": reviewer.model,
        "summary": summary_dict,
    }
    out_file.write_text(json.dumps(result_payload, indent=2), encoding="utf-8")
    print(f"Benchmark results successfully saved to: {out_file}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
