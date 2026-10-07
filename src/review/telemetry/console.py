"""Multi-tab interactive management console and trace waterfall inspector."""

import html
import json
from pathlib import Path
from typing import Any

TEMPLATE_PATH = Path(__file__).parent / "templates" / "console.html"


def render_dashboard_html(
    summary: dict[str, Any],
    recent_traces: list[dict[str, Any]],
) -> str:
    """Render a responsive, zero-external-dependency dark-themed operations console."""
    rev = summary.get("reviews", {})
    find = summary.get("findings", {})
    repro = summary.get("reproduction", {})
    mem = summary.get("memory", {})
    res = summary.get("resources", {})
    lat = summary.get("latency_ms", {})

    total_reviews = rev.get("total", 0)
    fp_rate = find.get("false_positive_filter_rate", 0.0) * 100.0
    repro_rate = repro.get("success_rate", 0.0) * 100.0
    precedent_rate = mem.get("precedent_citation_rate", 0.0) * 100.0
    total_cost = res.get("total_cost_usd", 0.0)
    avg_latency = lat.get("avg", 0.0) / 1000.0
    p50_latency = lat.get("p50", 0.0) / 1000.0
    p95_latency = lat.get("p95", 0.0) / 1000.0
    p99_latency = lat.get("p99", 0.0) / 1000.0

    # Build server-rendered initial traces JSON for instant client-side hydration
    initial_traces_json = json.dumps(recent_traces, default=str).replace("</", "<\\/")

    # Generate initial rows for server-side fallback
    trace_rows = []
    for t in recent_traces:
        repo_escaped = html.escape(t.get("repo") or "adhoc")
        pr_num = t.get("pull_number") or "-"
        pr_display = f"#{pr_num}" if pr_num != "-" else "Direct"
        status = t.get("status", "unknown")
        if status == "success":
            status_color = "#3fb950"
        elif status in ("empty_diff", "budget_halt"):
            status_color = "#d29922"
        else:
            status_color = "#f85149"
        dur_s = (t.get("total_duration_ms", 0.0)) / 1000.0
        cost = t.get("estimated_cost_usd", 0.0)
        tokens = t.get("total_tokens", 0)
        findings = t.get("findings_count", 0)
        proven = t.get("reproductions_succeeded", 0)
        precedents = t.get("precedents_cited", 0)
        trace_id = html.escape(t.get("trace_id", ""))

        pill_style = (
            f"background: {status_color}22; color: {status_color}; "
            f"border: 1px solid {status_color}44;"
        )
        row = (
            f'<tr data-trace-id="{trace_id}" class="trace-row" '
            f'onclick="inspectTrace(\'{trace_id}\')">\n'
            f'    <td style="font-family: monospace; font-weight: 600;">{repo_escaped}</td>\n'
            f'    <td><span class="badge">{pr_display}</span></td>\n'
            f'    <td><span class="status-pill" style="{pill_style}">{status}</span></td>\n'
            f"    <td>{dur_s:.2f}s</td>\n"
            f'    <td>${cost:.4f} <span class="muted">({tokens:,} tok)</span></td>\n'
            f"    <td><b>{findings}</b> findings</td>\n"
            f'    <td><span class="badge" style="background: #3b82f622; color: #60a5fa;">'
            f"🧪 {proven} proven</span></td>\n"
            f'    <td><span class="badge" style="background: #8b5cf622; color: #a78bfa;">'
            f"📚 {precedents} cited</span></td>\n"
            f"    <td><button class=\"action-btn\" onclick=\"event.stopPropagation(); "
            f"inspectTrace('{trace_id}')\">🔍 Inspect</button></td>\n"
            f"</tr>"
        )
        trace_rows.append(row)

    empty_msg = (
        '<tr><td colspan="9" style="text-align: center; color: #8b949e; padding: 28px;">'
        "No reviews recorded yet. Run a pull request review or trigger a webhook "
        "to populate telemetry.</td></tr>"
    )
    rows_html = "\n".join(trace_rows) if trace_rows else empty_msg

    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    replacements = {
        "{{TOTAL_REVIEWS}}": str(total_reviews),
        "{{FP_RATE}}": f"{fp_rate:.1f}",
        "{{REPRO_RATE}}": f"{repro_rate:.1f}",
        "{{PRECEDENT_RATE}}": f"{precedent_rate:.1f}",
        "{{TOTAL_COST}}": f"{total_cost:.4f}",
        "{{TOTAL_TOKENS}}": f"{res.get('total_tokens', 0):,}",
        "{{AVG_LATENCY}}": f"{avg_latency:.2f}",
        "{{P50_LATENCY}}": f"{p50_latency:.2f}",
        "{{P95_LATENCY}}": f"{p95_latency:.2f}",
        "{{P99_LATENCY}}": f"{p99_latency:.2f}",
        "{{REV_SUCCESSFUL}}": str(rev.get("successful", 0)),
        "{{REV_EMPTY_DIFF}}": str(rev.get("empty_diff", 0)),
        "{{REV_BUDGET_HALT}}": str(rev.get("budget_halt", 0)),
        "{{REV_FAILED}}": str(rev.get("failed", 0)),
        "{{PROMPT_TOKENS}}": f"{res.get('prompt_tokens', 0):,}",
        "{{COMPLETION_TOKENS}}": f"{res.get('completion_tokens', 0):,}",
        "{{FINDINGS_GENERATED}}": str(find.get("total_generated", 0)),
        "{{FINDINGS_REJECTED}}": str(find.get("rejected", 0)),
        "{{ROWS_HTML}}": rows_html,
        "{{INITIAL_TRACES_JSON}}": initial_traces_json,
    }

    content = template
    for key, val in replacements.items():
        content = content.replace(key, val)

    return content
