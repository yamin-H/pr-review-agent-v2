from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from review.api.deps import get_db_session
from review.api.main import create_app
from review.db.models import CalibrationMetricRecord, Repository, Tenant
from review.db.session import create_engine, drop_db, get_session_maker, init_db
from review.telemetry.console import render_dashboard_html
from review.telemetry.models import ReviewTrace, Span, SpanType
from review.telemetry.registry import MetricsRegistry


def test_render_dashboard_html_structure() -> None:
    """Test dashboard HTML generation contains all 4 tabs, KPI cards, and waterfall modal."""
    summary = {
        "reviews": {"total": 12, "successful": 10, "empty_diff": 1, "budget_halt": 1, "failed": 0},
        "findings": {"total_generated": 25, "rejected": 15, "false_positive_filter_rate": 0.60},
        "reproduction": {"attempted": 8, "succeeded": 6, "success_rate": 0.75},
        "memory": {"precedent_citation_rate": 0.33},
        "resources": {
            "total_cost_usd": 0.0452,
            "total_tokens": 124500,
            "prompt_tokens": 98000,
            "completion_tokens": 26500,
        },
        "latency_ms": {"avg": 3420.0, "p50": 3100.0, "p95": 5800.0, "p99": 6200.0},
    }

    recent_traces = [
        {
            "trace_id": "tr_alpha_01",
            "repo": "acme/backend",
            "pull_number": 101,
            "status": "success",
            "total_duration_ms": 3420.0,
            "estimated_cost_usd": 0.0035,
            "total_tokens": 12000,
            "findings_count": 2,
            "reproductions_succeeded": 1,
            "precedents_cited": 1,
            "spans": [
                {
                    "name": "llm_chunk_0",
                    "span_type": "llm_reasoning",
                    "start_time": 100.0,
                    "duration_ms": 1200.0,
                },
                {
                    "name": "verifier_eval",
                    "span_type": "verifier",
                    "start_time": 101.3,
                    "duration_ms": 800.0,
                },
                {
                    "name": "sandbox_exec",
                    "span_type": "sandbox",
                    "start_time": 102.2,
                    "duration_ms": 1420.0,
                },
            ],
        }
    ]

    html = render_dashboard_html(summary=summary, recent_traces=recent_traces)

    # 1. Structural tab checks
    assert "tab-overview" in html
    assert "tab-reviews" in html
    assert "tab-repos" in html
    assert "tab-memory" in html

    # 2. KPI Value replacements
    assert "12" in html  # Total reviews
    assert "60.0%" in html  # Precision filter rate
    assert "75.0%" in html  # Repro rate
    assert "33.0%" in html  # Precedent rate
    assert "$0.0452" in html  # Total cost
    assert "3.42s" in html  # Avg latency

    # 3. Waterfall and modal elements
    assert "trace-modal" in html
    assert "waterfall-container" in html
    assert "tr_alpha_01" in html
    assert "acme/backend" in html

    # 4. Zero external CDN dependencies
    assert "http://" not in html.lower() or "localhost" in html or "collector.local" in html
    assert "https://cdn" not in html
    assert "https://unpkg" not in html
    assert "https://cdnjs" not in html


def test_dashboard_route_in_fastapi() -> None:
    """Test /dashboard route returns 200 OK and HTML content type."""
    app = create_app()
    client = TestClient(app)

    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Autonomous PR Review Agent" in resp.text
    assert "Prometheus /metrics" in resp.text
    assert "tab-bar" in resp.text


def test_registry_integration_with_console() -> None:
    """Test MetricsRegistry feeds traces accurately into render_dashboard_html."""
    registry = MetricsRegistry()

    t = ReviewTrace(
        trace_id="trace_reg_99",
        repo="test/repo",
        pull_number=55,
        start_time=100.0,
    )
    t.spans.append(
        Span(
            name="static_analysis",
            span_type=SpanType.TOOL_EXECUTION,
            start_time=100.1,
            duration_ms=450.0,
        )
    )
    t.finish(status="success")
    registry.record_trace(t)

    html = render_dashboard_html(
        summary=registry.get_summary(),
        recent_traces=registry.get_recent_traces(),
    )
    assert "trace_reg_99" in html
    assert "test/repo" in html
    assert "#55" in html


@pytest.mark.asyncio
async def test_calibration_toggle_silence_api() -> None:
    """Test the POST toggle-silence API used by the console to silence/unsilence rules."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    maker = get_session_maker(engine)

    repo_id = "cal-test-repo-id"
    metric_id = "cal-test-metric-id"

    async with maker() as s:
        tenant = Tenant(id="tenant-cal", github_org_id="org_test_cal", name="Test Org")
        repo = Repository(
            id=repo_id,
            tenant_id="tenant-cal",
            full_name="org/cal-repo",
            github_repo_id=98765,
        )
        metric = CalibrationMetricRecord(
            id=metric_id,
            repository_id=repo_id,
            target_type="rule",
            target_key="rule:cve-sql-inject",
            acceptance_rate=0.25,
            total_signals=4,
            positive_signals=1,
            negative_signals=3,
            silenced=False,
        )
        s.add_all([tenant, repo, metric])
        await s.commit()

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db_session] = override_db
    client = TestClient(app)

    # 1. Toggle silence -> SILENCED (True)
    resp = client.post(f"/api/v1/repos/{repo_id}/calibration/{metric_id}/toggle-silence")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "updated"
    assert data["silenced"] is True
    assert data["target_key"] == "rule:cve-sql-inject"

    # 2. Toggle silence again -> UNSILENCED (False)
    resp2 = client.post(f"/api/v1/repos/{repo_id}/calibration/{metric_id}/toggle-silence")
    assert resp2.status_code == 200
    assert resp2.json()["silenced"] is False

    await drop_db(engine)
    await engine.dispose()
