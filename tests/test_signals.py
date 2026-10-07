"""Tests for developer feedback signals classification, aggregation, and calibration tracking."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from review.db.models import Base, Repository, Tenant
from review.memory.models import SignalFeedback
from review.memory.signals import (
    SignalTracker,
    classify_signal,
)


def test_classify_signal_reactions_and_actions() -> None:
    """Verify classification of developer feedback into positive, negative, or neutral."""
    # Positives
    assert classify_signal("+1", "reacted") == 1
    assert classify_signal("heart", "reacted") == 1
    assert classify_signal("rocket", "reacted") == 1
    assert classify_signal("hooray", "reacted") == 1
    assert classify_signal(None, "resolved") == 1

    # Negatives
    assert classify_signal("-1", "reacted") == -1
    assert classify_signal("confused", "reacted") == -1
    assert classify_signal(None, "dismissed") == -1

    # Neutrals
    assert classify_signal("eyes", "reacted") == 0
    assert classify_signal("laugh", "reacted") == 0
    assert classify_signal(None, "outdated") == 0
    assert classify_signal(None, "reacted") == 0


@pytest.mark.asyncio
async def test_signal_tracker_aggregation_and_silencing() -> None:
    """Verify SignalTracker computes acceptance rates and flags silenced categories."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        # Seed Repo
        tenant = Tenant(id="tenant-sig-1", github_org_id="sigorg", name="Sig Org")
        repo = Repository(
            id="repo-sig-1",
            tenant_id="tenant-sig-1",
            full_name="sigorg/api-service",
            github_repo_id=555,
        )
        session.add_all([tenant, repo])
        await session.commit()

        tracker = SignalTracker(session=session, silence_threshold=0.40, min_signals=3)

        # 1. Record 1 positive signal for 'security'
        fb1 = SignalFeedback(
            repository_id="repo-sig-1",
            pull_number=10,
            reaction="+1",
            action="reacted",
            rule_id="no-hardcoded-secret",
            rule_category="security",
        )
        await tracker.record_feedback(fb1)
        await session.commit()

        rate = await tracker.get_acceptance_rate("repo-sig-1", rule_category="security")
        # 1 positive / 1 total = 1.0 (though min_signals is 3, default prior is 1.0)
        assert rate == 1.0
        assert await tracker.is_silenced("repo-sig-1", rule_category="security") is False

        # 2. Record negative signals for a noisy style rule 'import-sorting'
        for i in range(3):
            fb_neg = SignalFeedback(
                repository_id="repo-sig-1",
                pull_number=10 + i,
                reaction="-1",
                action="dismissed",
                rule_id="import-sorting",
                rule_category="style",
            )
            await tracker.record_feedback(fb_neg)
        await session.commit()

        # Check 'import-sorting' rule: 0 positive, 3 negative -> acceptance 0.0 -> silenced!
        rule_metric = await tracker.get_metric("repo-sig-1", "rule", "import-sorting")
        assert rule_metric is not None
        assert rule_metric.negative_signals == 3
        assert rule_metric.positive_signals == 0
        assert rule_metric.acceptance_rate == 0.0
        assert rule_metric.silenced is True

        assert await tracker.is_silenced("repo-sig-1", rule_id="import-sorting") is True
        assert (
            await tracker.get_acceptance_rate("repo-sig-1", rule_id="import-sorting")
            == 0.0
        )

        # 3. Add 1 positive signal for 'import-sorting': 1/4 = 0.25 < 0.40 -> still silenced
        fb_pos = SignalFeedback(
            repository_id="repo-sig-1",
            pull_number=20,
            reaction="+1",
            action="reacted",
            rule_id="import-sorting",
            rule_category="style",
        )
        await tracker.record_feedback(fb_pos)
        await session.commit()

        assert await tracker.is_silenced("repo-sig-1", rule_id="import-sorting") is True
        assert (
            await tracker.get_acceptance_rate("repo-sig-1", rule_id="import-sorting")
            == 0.25
        )

        # 4. Add 4 more positive signals: 5 pos / 8 total = 0.625 >= 0.40 -> no longer silenced!
        for i in range(4):
            fb_pos_more = SignalFeedback(
                repository_id="repo-sig-1",
                pull_number=21 + i,
                reaction="+1",
                action="resolved",
                rule_id="import-sorting",
                rule_category="style",
            )
            await tracker.record_feedback(fb_pos_more)
        await session.commit()

        assert await tracker.is_silenced("repo-sig-1", rule_id="import-sorting") is False
        assert (
            await tracker.get_acceptance_rate("repo-sig-1", rule_id="import-sorting")
            == 0.625
        )

    await engine.dispose()
