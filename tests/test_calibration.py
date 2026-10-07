"""Tests for Calibrated Silence Engine and finding severity adjustment."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from review.db.models import Base, Repository, Tenant
from review.findings import Finding, Severity
from review.memory.calibration import CalibrationEngine
from review.memory.models import SignalFeedback
from review.memory.signals import SignalTracker


def test_sync_calibration_with_rates() -> None:
    """Verify in-memory sync calibration properly silences, degrades, and exempts findings."""
    calibrator = CalibrationEngine(silence_threshold=0.40, degrade_threshold=0.70)

    f_bug_reproduced = Finding(
        file="src/auth.py",
        line=10,
        title="Zero division on empty auth header",
        body="Division by zero encountered when payload length is 0",
        severity=Severity.HIGH,
        is_reproduced=True,
        reproduction_output="ZeroDivisionError: division by zero",
        rule_id="zero-division",
    )

    f_precedent = Finding(
        file="src/db.py",
        line=25,
        title="Missing query parameter binding",
        body="SQL parameter formatting allows injection",
        severity=Severity.HIGH,
        citation="CVE-2023-1111",
        rule_id="sql-bind",
    )

    f_noisy_nitpick = Finding(
        file="src/utils.py",
        line=5,
        title="Missing function docstring",
        body="All helper functions should have docstrings",
        severity=Severity.MEDIUM,
        rule_id="missing-docstring",
    )

    f_marginal = Finding(
        file="src/service.py",
        line=50,
        title="Variable name could be more descriptive",
        body="Consider renaming 'x' to 'user_count'",
        severity=Severity.MEDIUM,
        rule_id="variable-naming",
    )

    f_trusted = Finding(
        file="src/crypto.py",
        line=100,
        title="Weak random generator used for secret key",
        body="Use secrets.token_bytes instead of random.random",
        severity=Severity.HIGH,
        rule_id="weak-rng",
    )

    acceptance_rates = {
        "zero-division": 0.10,      # Low rate, but reproduced -> MUST BE KEPT
        "sql-bind": 0.20,           # Low rate, but cited -> MUST BE KEPT
        "missing-docstring": 0.25,  # Low rate -> MUST BE SILENCED
        "variable-naming": 0.55,    # Marginal rate -> MUST BE DOWNGRADED
        "weak-rng": 0.95,           # High rate -> KEPT AS HIGH
    }

    calibrated = calibrator.sync_calibrate_with_rates(
        findings=[f_bug_reproduced, f_precedent, f_noisy_nitpick, f_marginal, f_trusted],
        acceptance_rates=acceptance_rates,
    )

    # 4 published, 1 silenced
    assert len(calibrated.published) == 4
    assert len(calibrated.silenced) == 1
    assert calibrated.silenced_count == 1
    assert calibrated.downgraded_count == 1

    # Verify silenced finding
    assert calibrated.silenced[0].rule_id == "missing-docstring"

    # Verify reproduced bug is published and preserved as HIGH
    published_by_rule = {f.rule_id: f for f in calibrated.published}
    assert "zero-division" in published_by_rule
    assert published_by_rule["zero-division"].severity == Severity.HIGH

    # Verify precedent is preserved
    assert "sql-bind" in published_by_rule
    assert published_by_rule["sql-bind"].severity == Severity.HIGH

    # Verify marginal finding is downgraded to LOW
    assert "variable-naming" in published_by_rule
    assert published_by_rule["variable-naming"].severity == Severity.LOW

    # Verify trusted finding remains HIGH
    assert "weak-rng" in published_by_rule
    assert published_by_rule["weak-rng"].severity == Severity.HIGH


@pytest.mark.asyncio
async def test_async_calibration_engine_with_signal_tracker() -> None:
    """Verify end-to-end async calibration querying database metrics."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        # Seed Repo
        tenant = Tenant(id="tenant-calib-1", github_org_id="caliborg", name="Calib Org")
        repo = Repository(
            id="repo-calib-1",
            tenant_id="tenant-calib-1",
            full_name="caliborg/payments",
            github_repo_id=789,
        )
        session.add_all([tenant, repo])
        await session.commit()

        tracker = SignalTracker(session=session, silence_threshold=0.40, min_signals=3)

        # Record 3 negative dismissals for 'nitpick-whitespace'
        for i in range(3):
            await tracker.record_feedback(
                SignalFeedback(
                    repository_id="repo-calib-1",
                    pull_number=100 + i,
                    reaction="-1",
                    action="dismissed",
                    rule_id="nitpick-whitespace",
                    rule_category="style",
                )
            )

        # Record mixed signals for 'naming-convention': 2 pos, 2 neg (rate = 0.50)
        for i in range(2):
            await tracker.record_feedback(
                SignalFeedback(
                    repository_id="repo-calib-1",
                    pull_number=200 + i,
                    reaction="+1",
                    action="resolved",
                    rule_id="naming-convention",
                    rule_category="style",
                )
            )
            await tracker.record_feedback(
                SignalFeedback(
                    repository_id="repo-calib-1",
                    pull_number=210 + i,
                    reaction="-1",
                    action="dismissed",
                    rule_id="naming-convention",
                    rule_category="style",
                )
            )
        await session.commit()

        calibrator = CalibrationEngine(signal_tracker=tracker)

        findings = [
            Finding(
                file="payments.py",
                line=12,
                title="Trailing whitespace detected",
                body="Remove trailing whitespace",
                severity=Severity.LOW,
                rule_id="nitpick-whitespace",
            ),
            Finding(
                file="payments.py",
                line=45,
                title="Non-standard variable name",
                body="Rename variable to camelCase or snake_case",
                severity=Severity.MEDIUM,
                rule_id="naming-convention",
            ),
            Finding(
                file="payments.py",
                line=80,
                title="Unhandled exception crash in payment gateway",
                body="Null pointer crash when payload lacks token",
                severity=Severity.HIGH,
                is_reproduced=True,
                reproduction_output="RuntimeError: Gateway token missing",
                rule_id="unhandled-exception",
            ),
        ]

        result = await calibrator.calibrate(findings=findings, repo_identifier="repo-calib-1")

        # Trailing whitespace silenced
        assert result.silenced_count == 1
        assert result.silenced[0].rule_id == "nitpick-whitespace"

        # Naming convention downgraded to LOW
        assert result.downgraded_count == 1
        naming_finding = next(f for f in result.published if f.rule_id == "naming-convention")
        assert naming_finding.severity == Severity.LOW
        assert "historical acceptance: 50%" in naming_finding.body

        # Unhandled exception reproduced crash published
        crash_finding = next(f for f in result.published if f.rule_id == "unhandled-exception")
        assert crash_finding.severity == Severity.HIGH

    await engine.dispose()
