"""Developer feedback signals ingestion and aggregation tracker."""

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from review.db.models import CalibrationMetricRecord, Repository, ReviewFeedbackRecord
from review.memory.models import SignalFeedback

logger = logging.getLogger("review.memory.signals")

POSITIVE_REACTIONS = {"+1", "heart", "rocket", "hooray"}
NEGATIVE_REACTIONS = {"-1", "confused"}
POSITIVE_ACTIONS = {"resolved"}
NEGATIVE_ACTIONS = {"dismissed"}
DEFAULT_SILENCE_THRESHOLD = 0.40
MIN_SIGNALS_FOR_SILENCE = 3


def classify_signal(reaction: str | None, action: str) -> int:
    """Classify a signal into +1 (positive), -1 (negative), or 0 (neutral)."""
    reaction_clean = (reaction or "").strip().lower()
    action_clean = (action or "").strip().lower()

    if action_clean in POSITIVE_ACTIONS or reaction_clean in POSITIVE_REACTIONS:
        return 1
    if action_clean in NEGATIVE_ACTIONS or reaction_clean in NEGATIVE_REACTIONS:
        return -1
    return 0


class SignalTracker:
    """Tracks developer feedback signals and computes dynamic acceptance calibration metrics."""

    def __init__(
        self,
        session: AsyncSession,
        silence_threshold: float = DEFAULT_SILENCE_THRESHOLD,
        min_signals: int = MIN_SIGNALS_FOR_SILENCE,
    ) -> None:
        self.session = session
        self.silence_threshold = silence_threshold
        self.min_signals = min_signals

    async def _resolve_repo_id(self, repo_identifier: str) -> str:
        """Resolve repository UUID from either repository ID or full_name."""
        clean = repo_identifier.lower().strip()
        stmt = select(Repository).where(
            (Repository.id == repo_identifier) | (func.lower(Repository.full_name) == clean)
        )
        res = await self.session.execute(stmt)
        repo = res.scalar_one_or_none()
        if repo:
            return repo.id
        raise ValueError(f"Repository not found for identifier: {repo_identifier}")

    async def record_feedback(
        self, feedback: SignalFeedback
    ) -> tuple[ReviewFeedbackRecord, list[CalibrationMetricRecord]]:
        """Persist a developer feedback signal and update calibration metrics across hierarchies."""
        repo_id = await self._resolve_repo_id(feedback.repository_id)

        feedback_rec = ReviewFeedbackRecord(
            repository_id=repo_id,
            review_run_id=feedback.review_run_id,
            finding_id=feedback.finding_id,
            pull_number=feedback.pull_number,
            comment_id=feedback.comment_id,
            reaction=feedback.reaction,
            action=feedback.action,
            user_login=feedback.user_login,
            rule_id=feedback.rule_id,
            rule_category=feedback.rule_category,
            created_at=feedback.created_at,
        )
        self.session.add(feedback_rec)
        await self.session.flush()

        # Update targets: repo, category (if set), rule (if set)
        targets: list[tuple[str, str]] = [("repo", repo_id)]
        if feedback.rule_category:
            targets.append(("category", feedback.rule_category.lower().strip()))
        if feedback.rule_id:
            targets.append(("rule", feedback.rule_id.lower().strip()))

        updated_metrics: list[CalibrationMetricRecord] = []
        for target_type, target_key in targets:
            metric = await self._recalculate_metric(repo_id, target_type, target_key)
            updated_metrics.append(metric)

        await self.session.flush()
        return feedback_rec, updated_metrics

    async def _recalculate_metric(
        self, repo_id: str, target_type: str, target_key: str
    ) -> CalibrationMetricRecord:
        """Recalculate signal totals and acceptance rate for a target scope."""
        stmt = select(ReviewFeedbackRecord).where(ReviewFeedbackRecord.repository_id == repo_id)
        if target_type == "category":
            stmt = stmt.where(func.lower(ReviewFeedbackRecord.rule_category) == target_key)
        elif target_type == "rule":
            stmt = stmt.where(func.lower(ReviewFeedbackRecord.rule_id) == target_key)

        res = await self.session.execute(stmt)
        records = res.scalars().all()

        pos_count = 0
        neg_count = 0
        total_count = len(records)

        for rec in records:
            classification = classify_signal(rec.reaction, rec.action)
            if classification > 0:
                pos_count += 1
            elif classification < 0:
                neg_count += 1

        active_signals = pos_count + neg_count
        acceptance_rate = float(pos_count / active_signals) if active_signals > 0 else 1.0

        is_silenced = bool(
            active_signals >= self.min_signals and acceptance_rate < self.silence_threshold
        )

        # Upsert into calibration_metrics
        metric_stmt = select(CalibrationMetricRecord).where(
            (CalibrationMetricRecord.repository_id == repo_id)
            & (CalibrationMetricRecord.target_type == target_type)
            & (CalibrationMetricRecord.target_key == target_key)
        )
        metric_res = await self.session.execute(metric_stmt)
        metric_rec = metric_res.scalar_one_or_none()

        if metric_rec:
            metric_rec.total_signals = total_count
            metric_rec.positive_signals = pos_count
            metric_rec.negative_signals = neg_count
            metric_rec.acceptance_rate = acceptance_rate
            metric_rec.silenced = is_silenced
            metric_rec.updated_at = datetime.now(UTC)
        else:
            metric_rec = CalibrationMetricRecord(
                repository_id=repo_id,
                target_type=target_type,
                target_key=target_key,
                total_signals=total_count,
                positive_signals=pos_count,
                negative_signals=neg_count,
                acceptance_rate=acceptance_rate,
                silenced=is_silenced,
                updated_at=datetime.now(UTC),
            )
            self.session.add(metric_rec)

        logger.info(
            "Recalculated calibration [%s:%s] repo=%s: pos=%d, neg=%d, rate=%.2f, silenced=%s",
            target_type,
            target_key,
            repo_id,
            pos_count,
            neg_count,
            acceptance_rate,
            is_silenced,
        )
        return metric_rec

    async def get_metric(
        self, repo_identifier: str, target_type: str, target_key: str
    ) -> CalibrationMetricRecord | None:
        """Retrieve stored calibration metric for a specific target."""
        repo_id = await self._resolve_repo_id(repo_identifier)
        stmt = select(CalibrationMetricRecord).where(
            (CalibrationMetricRecord.repository_id == repo_id)
            & (CalibrationMetricRecord.target_type == target_type)
            & (CalibrationMetricRecord.target_key == target_key.lower().strip())
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_acceptance_rate(
        self,
        repo_identifier: str,
        rule_category: str | None = None,
        rule_id: str | None = None,
    ) -> float:
        """Get effective acceptance rate, checking rule first, then category, then repo."""
        repo_id = await self._resolve_repo_id(repo_identifier)

        if rule_id:
            m = await self.get_metric(repo_id, "rule", rule_id)
            if m and (m.positive_signals + m.negative_signals) >= self.min_signals:
                return m.acceptance_rate

        if rule_category:
            m = await self.get_metric(repo_id, "category", rule_category)
            if m and (m.positive_signals + m.negative_signals) >= self.min_signals:
                return m.acceptance_rate

        repo_m = await self.get_metric(repo_id, "repo", repo_id)
        if repo_m and (repo_m.positive_signals + repo_m.negative_signals) >= self.min_signals:
            return repo_m.acceptance_rate

        return 1.0

    async def is_silenced(
        self,
        repo_identifier: str,
        rule_category: str | None = None,
        rule_id: str | None = None,
    ) -> bool:
        """Determine if a rule or category is flagged as silenced."""
        repo_id = await self._resolve_repo_id(repo_identifier)

        if rule_id:
            m = await self.get_metric(repo_id, "rule", rule_id)
            if m and m.silenced:
                return True

        if rule_category:
            m = await self.get_metric(repo_id, "category", rule_category)
            if m and m.silenced:
                return True

        return False
