"""Self-contained operational dashboard HTML generator for review metrics and traces."""

import html
from typing import Any


def render_dashboard_html(
    summary: dict[str, Any],
    recent_traces: list[dict[str, Any]],
) -> str:
    """Render a standalone, zero-dependency dark-themed HTML monitoring dashboard."""
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

    trace_rows = []
    for t in recent_traces:
        repo_escaped = html.escape(t.get("repo") or "adhoc")
        pr_num = t.get("pull_number") or "-"
        pr_display = f"#{pr_num}" if pr_num != "-" else "Direct"
        status = t.get("status", "unknown")
        if status == "success":
            status_color = "#10b981"
        elif status == "empty_diff":
            status_color = "#f59e0b"
        else:
            status_color = "#ef4444"
        dur_s = (t.get("total_duration_ms", 0.0)) / 1000.0
        cost = t.get("estimated_cost_usd", 0.0)
        tokens = t.get("total_tokens", 0)
        findings = t.get("findings_count", 0)
        proven = t.get("reproductions_succeeded", 0)
        precedents = t.get("precedents_cited", 0)

        pill_style = (
            f"background: {status_color}22; color: {status_color}; "
            f"border: 1px solid {status_color}44;"
        )
        trace_rows.append(
            f"""
            <tr>
                <td style="font-family: monospace; font-weight: 600;">{repo_escaped}</td>
                <td><span class="badge">{pr_display}</span></td>
                <td><span class="status-pill" style="{pill_style}">{status}</span></td>
                <td>{dur_s:.2f}s</td>
                <td>${cost:.4f} <span class="muted">({tokens:,} tok)</span></td>
                <td><b>{findings}</b> findings</td>
                <td>
                    <span class="badge" style="background: #3b82f622; color: #60a5fa;">
                        🧪 {proven} proven
                    </span>
                </td>
                <td>
                    <span class="badge" style="background: #8b5cf622; color: #a78bfa;">
                        📚 {precedents} cited
                    </span>
                </td>
            </tr>
            """
        )

    empty_msg = (
        '<tr><td colspan="8" style="text-align: center; color: #94a3b8; padding: 24px;">'
        "No reviews recorded yet. Run a pull request review to populate telemetry.</td></tr>"
    )
    rows_html = "\n".join(trace_rows) if trace_rows else empty_msg

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Autonomous Review Agent | Operational Telemetry</title>
    <style>
        :root {{
            --bg-color: #0d1117;
            --card-bg: #161b22;
            --border-color: #30363d;
            --text-color: #c9d1d9;
            --text-heading: #f0f6fc;
            --accent-blue: #58a6ff;
            --accent-green: #238636;
            --accent-purple: #8957e5;
            --accent-orange: #d29922;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background-color: var(--bg-color);
            color: var(--text-color);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            line-height: 1.5;
            padding: 24px;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 16px;
            margin-bottom: 24px;
        }}
        .header h1 {{
            color: var(--text-heading);
            font-size: 24px;
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .live-tag {{
            font-size: 12px;
            background: #23863622;
            color: #3fb950;
            border: 1px solid #238636;
            padding: 2px 8px;
            border-radius: 12px;
            font-weight: 600;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 28px;
        }}
        .card {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 20px;
        }}
        .card .title {{
            font-size: 13px;
            color: #8b949e;
            text-transform: uppercase;
            font-weight: 600;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
        }}
        .card .value {{
            font-size: 28px;
            font-weight: 700;
            color: var(--text-heading);
        }}
        .card .sub {{
            font-size: 12px;
            color: #8b949e;
            margin-top: 4px;
        }}
        .section-title {{
            font-size: 18px;
            font-weight: 600;
            color: var(--text-heading);
            margin-bottom: 12px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            overflow: hidden;
        }}
        th, td {{
            padding: 12px 16px;
            text-align: left;
            border-bottom: 1px solid var(--border-color);
            font-size: 14px;
        }}
        th {{
            background: #21262d;
            color: #8b949e;
            font-weight: 600;
            font-size: 12px;
            text-transform: uppercase;
        }}
        tr:hover td {{
            background: #1c2128;
        }}
        .badge {{
            background: #30363d;
            color: #c9d1d9;
            padding: 2px 8px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 500;
        }}
        .status-pill {{
            padding: 2px 10px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
        }}
        .muted {{ color: #8b949e; font-size: 12px; }}
        .refresh-btn {{
            background: #21262d;
            border: 1px solid var(--border-color);
            color: var(--text-heading);
            padding: 6px 14px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            font-weight: 600;
            text-decoration: none;
        }}
        .refresh-btn:hover {{ background: #30363d; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>
            <span>🛡️ PR Review Agent Observability</span>
            <span class="live-tag">● LIVE</span>
        </h1>
        <div>
            <a href="/dashboard" class="refresh-btn">⟳ Refresh</a>
            <a href="/metrics" class="refresh-btn" style="margin-left: 8px;">Prometheus</a>
            <a href="/api/v1/metrics" class="refresh-btn" style="margin-left: 8px;">JSON API</a>
        </div>
    </div>

    <div class="grid">
        <div class="card">
            <div class="title">Total PR Reviews</div>
            <div class="value">{total_reviews}</div>
            <div class="sub">Completed runs</div>
        </div>
        <div class="card">
            <div class="title">Precision Filter Rate</div>
            <div class="value" style="color: #3fb950;">{fp_rate:.1f}%</div>
            <div class="sub">False positives pruned</div>
        </div>
        <div class="card">
            <div class="title">Proof by Execution</div>
            <div class="value" style="color: #58a6ff;">{repro_rate:.1f}%</div>
            <div class="sub">Sandbox confirmed bugs</div>
        </div>
        <div class="card">
            <div class="title">Precedents Cited</div>
            <div class="value" style="color: #a371f7;">{precedent_rate:.1f}%</div>
            <div class="sub">Repo memory grounded</div>
        </div>
        <div class="card">
            <div class="title">Cumulative Cost</div>
            <div class="value">${total_cost:.4f}</div>
            <div class="sub">Tokens: {res.get("total_tokens", 0):,}</div>
        </div>
        <div class="card">
            <div class="title">Avg Review Latency</div>
            <div class="value">{avg_latency:.2f}s</div>
            <div class="sub">P95: {(lat.get("p95", 0.0) / 1000.0):.2f}s</div>
        </div>
    </div>

    <div class="section-title">Recent Pull Request Review Traces</div>
    <table>
        <thead>
            <tr>
                <th>Repository</th>
                <th>PR #</th>
                <th>Status</th>
                <th>Duration</th>
                <th>Cost & Tokens</th>
                <th>Findings</th>
                <th>Sandbox Proof</th>
                <th>Memory Citation</th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
</body>
</html>
"""
