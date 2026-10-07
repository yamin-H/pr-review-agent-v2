import argparse
import json
import sys
from pathlib import Path

from review.chunker import chunk_diff
from review.diff import added_lines
from review.findings import Finding, ReviewOutput
from review.llm import GroqReviewer
from review.sandbox.prover import ProofEngine
from review.sandbox.synthesizer import ReproductionSynthesizer
from review.telemetry.models import ReviewTrace
from review.telemetry.tracker import TelemetryTracker
from review.validate import validate_finding_lines
from review.verifier import FindingVerifier


def review_diff(
    diff_text: str,
    reviewer: GroqReviewer | None = None,
    pr_title: str | None = None,
    pr_description: str | None = None,
    verifier: FindingVerifier | None = None,
    proof_engine: ProofEngine | None = None,
    verify: bool = False,
    reproduce: bool = False,
    repo_root: Path | None = None,
) -> ReviewOutput:
    """Run an end-to-end review on a unified diff text string."""
    if reviewer is None:
        reviewer = GroqReviewer()

    chunks = chunk_diff(diff_text)
    if not chunks:
        return ReviewOutput(
            summary="No reviewable changes found in the provided diff.",
            findings=[],
        )

    # Ground truth valid added lines from the diff
    parsed_lines = added_lines(diff_text)
    valid_lines = {(item.path, item.line) for item in parsed_lines}

    all_valid_findings: list[Finding] = []
    chunk_summaries: list[str] = []

    for chunk in chunks:
        chunk_prompt_text = chunk.format_for_prompt()
        chunk_review = reviewer.review_chunk(
            diff_chunk_text=chunk_prompt_text,
            pr_title=pr_title,
            pr_description=pr_description,
        )

        valid_findings, _ = validate_finding_lines(chunk_review.findings, valid_lines)
        all_valid_findings.extend(valid_findings)

        if chunk_review.summary:
            chunk_summaries.append(chunk_review.summary)

    # Optional adversarial verification step to filter false positives
    if verify and all_valid_findings:
        if verifier is None:
            verifier = FindingVerifier(reviewer=reviewer)
        verified_findings, _ = verifier.verify_findings(
            findings=all_valid_findings,
            diff_text=diff_text,
            pr_title=pr_title or "",
            pr_description=pr_description or "",
        )
        all_valid_findings = verified_findings

    # Optional reproduction proof step in execution sandbox
    if reproduce and all_valid_findings:
        if proof_engine is None:
            proof_engine = ProofEngine(
                synthesizer=ReproductionSynthesizer(reviewer=reviewer)
            )
        all_valid_findings = proof_engine.prove_findings(
            findings=all_valid_findings,
            diff_text=diff_text,
            repo_root=repo_root,
        )

    consolidated_summary = (
        " ".join(chunk_summaries)
        if chunk_summaries
        else "Review complete with no critical findings."
    )

    return ReviewOutput(
        summary=consolidated_summary,
        findings=all_valid_findings,
    )


def print_human_report(
    review: ReviewOutput,
    trace: ReviewTrace | None = None,
) -> None:
    """Print review results in a clean, professional human-readable terminal format."""
    print("=" * 80)
    print("AUTONOMOUS PR REVIEW REPORT")
    print("=" * 80)
    print(f"\nSummary:\n{review.summary}\n")

    if not review.findings:
        print("No inline findings reported. Changes look good.")
    else:
        print(f"Findings ({len(review.findings)}):")
        for i, finding in enumerate(review.findings, 1):
            severity_tag = f"[{finding.severity.value.upper()}]"
            repro_tag = " [PROVEN IN SANDBOX]" if finding.is_reproduced else ""
            citation_tag = f" [Cited: {finding.citation}]" if finding.citation else ""
            print(
                f"\n  {i}. {severity_tag}{repro_tag}{citation_tag} "
                f"{finding.file}:{finding.line} - {finding.title}"
            )
            print(f"     {finding.body}")
            if finding.reproduction_output:
                print(f"     [Sandbox Proof Output]: {finding.reproduction_output[:120]}...")

    if trace:
        print("\n" + "-" * 80)
        print("EXECUTION TELEMETRY & COST")
        print("-" * 80)
        dur_s = trace.total_duration_ms / 1000.0
        print(f"  Duration:           {dur_s:.2f}s")
        print(
            f"  Tokens Consumed:    {trace.total_tokens:,} "
            f"(Prompt: {trace.prompt_tokens:,}, Output: {trace.completion_tokens:,})"
        )
        print(f"  Estimated Cost:     ${trace.estimated_cost_usd:.6f}")
        bd = trace.get_latency_breakdown()
        if any(v > 0 for v in bd.values()):
            print("  Latency Breakdown:")
            for k, ms in bd.items():
                if ms > 0:
                    print(f"    - {k}: {ms / 1000.0:.2f}s")

    print("\n" + "=" * 80)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for running reviews on diff files or piped stdin."""
    parser = argparse.ArgumentParser(description="Autonomous PR Review Agent (v2) - CLI")
    parser.add_argument(
        "diff_file",
        nargs="?",
        help="Path to a unified diff/patch file (or reads from stdin if omitted)",
    )
    parser.add_argument("--model", help="Groq model name override")
    parser.add_argument("--title", help="Optional PR title")
    parser.add_argument("--description", help="Optional PR description")
    parser.add_argument(
        "--agent",
        action="store_true",
        help="Run the autonomous Plan-Act-Observe agent loop with tools",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Run adversarial verification filter to challenge and drop false positives",
    )
    parser.add_argument(
        "--reproduce",
        action="store_true",
        help="Synthesize and run minimal reproduction tests in the execution sandbox",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of formatted report",
    )

    args = parser.parse_args(argv)

    if args.diff_file:
        diff_path = Path(args.diff_file)
        if not diff_path.exists():
            print(f"Error: Diff file not found: {args.diff_file}", file=sys.stderr)
            return 1
        diff_text = diff_path.read_text(encoding="utf-8")
    else:
        if sys.stdin.isatty():
            parser.print_help()
            print("\nError: No diff file provided and stdin is empty.", file=sys.stderr)
            return 1
        diff_text = sys.stdin.read()

    reviewer = GroqReviewer(model=args.model)
    tracker = TelemetryTracker(repo=Path.cwd().name) if args.agent else None

    try:
        if args.agent:
            from review.agent.graph import AgentRunner

            runner = AgentRunner(
                reviewer=reviewer,
                enable_verifier=args.verify,
                enable_reproduction=args.reproduce,
            )
            result = runner.review_to_output(
                diff_text=diff_text,
                repo_root=Path.cwd(),
                repo=Path.cwd().name,
                pr_title=args.title or "",
                pr_description=args.description or "",
                tracker=tracker,
            )
        else:
            result = review_diff(
                diff_text=diff_text,
                reviewer=reviewer,
                pr_title=args.title,
                pr_description=args.description,
                verify=args.verify,
                reproduce=args.reproduce,
            )
    except Exception as e:
        print(f"Error executing review: {e}", file=sys.stderr)
        return 1

    trace = tracker.finish(
        status="success", findings_count=len(result.findings)
    ) if tracker else None

    if args.json:
        payload = result.model_dump()
        if trace:
            payload["telemetry"] = trace.model_dump()
        print(json.dumps(payload, indent=2))
    else:
        print_human_report(result, trace=trace)

    return 0


if __name__ == "__main__":
    sys.exit(main())
