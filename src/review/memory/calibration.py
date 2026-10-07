"""Calibrated silence engine for intelligent finding filtering and severity adjustment."""

import logging
from dataclasses import dataclass, field

from review.findings import Finding, Severity
from review.memory.signals import SignalTracker

logger = logging.getLogger("review.memory.calibration")

DEFAULT_SILENCE_THRESHOLD = 0.40
DEFAULT_DEGRADE_THRESHOLD = 0.70


@dataclass
class CalibratedFindings:
    """Result of running findings through the calibrated silence filter."""

    published: list[Finding] = field(default_factory=list)
    silenced: list[Finding] = field(default_factory=list)
    downgraded_count: int = 0
    silenced_count: int = 0
    audit_log: list[str] = field(default_factory=list)


class CalibrationEngine:
    """Filters noisy nitpicks and calibrates finding severity based on developer acceptance."""

    def __init__(
        self,
        signal_tracker: SignalTracker | None = None,
        silence_threshold: float = DEFAULT_SILENCE_THRESHOLD,
        degrade_threshold: float = DEFAULT_DEGRADE_THRESHOLD,
    ) -> None:
        self.signal_tracker = signal_tracker
        self.silence_threshold = silence_threshold
        self.degrade_threshold = degrade_threshold

    async def calibrate(
        self,
        findings: list[Finding],
        repo_identifier: str,
    ) -> CalibratedFindings:
        """Evaluate findings against calibration metrics and filter or degrade accordingly."""
        result = CalibratedFindings()

        for finding in findings:
            # Rule 1: Sandbox reproduction exemption (ground truth bugs are never silenced)
            if finding.is_reproduced is True or (
                finding.reproduction_output and "Error" in finding.reproduction_output
            ):
                result.published.append(finding)
                result.audit_log.append(
                    f"KEPT '{finding.title}': Sandbox-verified reproducible defect."
                )
                continue

            # Rule 2: Precedent citation exemption (explicit historical team rules are preserved)
            if finding.citation:
                result.published.append(finding)
                result.audit_log.append(
                    f"KEPT '{finding.title}': Backed by precedent [{finding.citation}]."
                )
                continue

            # If no signal tracker is configured, keep finding by default
            if not self.signal_tracker:
                result.published.append(finding)
                continue

            # Check silencing and acceptance rate
            is_silenced = await self.signal_tracker.is_silenced(
                repo_identifier=repo_identifier,
                rule_category=finding.rule_category,
                rule_id=finding.rule_id,
            )
            acceptance_rate = await self.signal_tracker.get_acceptance_rate(
                repo_identifier=repo_identifier,
                rule_category=finding.rule_category,
                rule_id=finding.rule_id,
            )

            # Rule 3: Silence noisy nitpicks
            if is_silenced or acceptance_rate < self.silence_threshold:
                result.silenced.append(finding)
                result.silenced_count += 1
                msg = (
                    f"SILENCED '{finding.title}' (Rule: {finding.rule_id or 'none'}, "
                    f"Category: {finding.rule_category or 'none'}): "
                    f"Acceptance rate {acceptance_rate:.2f} < {self.silence_threshold:.2f}."
                )
                result.audit_log.append(msg)
                logger.info(msg)
                continue

            # Rule 4: Degrade marginal / questionable findings
            if acceptance_rate < self.degrade_threshold:
                original_severity = finding.severity
                finding.severity = Severity.LOW
                finding.body += (
                    "\n\n> ℹ️ *Note: Severity adjusted to low based on feedback calibration "
                    f"(historical acceptance: {int(acceptance_rate * 100)}%).*"
                )
                result.published.append(finding)
                result.downgraded_count += 1
                result.audit_log.append(
                    f"DOWNGRADED '{finding.title}' from {original_severity} to {finding.severity} "
                    f"(acceptance rate {acceptance_rate:.2f} < {self.degrade_threshold:.2f})."
                )
                continue

            # Rule 5: Keep normal high-acceptance finding
            result.published.append(finding)
            result.audit_log.append(
                f"KEPT '{finding.title}': High acceptance rate ({acceptance_rate:.2f})."
            )

        return result

    def sync_calibrate_with_rates(
        self,
        findings: list[Finding],
        acceptance_rates: dict[str, float],
        silenced_keys: set[str] | None = None,
    ) -> CalibratedFindings:
        """Synchronous in-memory calibration using a pre-fetched dictionary of rates."""
        result = CalibratedFindings()
        silenced_set = silenced_keys or set()

        for finding in findings:
            if finding.is_reproduced is True or (
                finding.reproduction_output and "Error" in finding.reproduction_output
            ):
                result.published.append(finding)
                result.audit_log.append(f"KEPT '{finding.title}': Sandbox-verified.")
                continue

            if finding.citation:
                result.published.append(finding)
                result.audit_log.append(f"KEPT '{finding.title}': Precedent cited.")
                continue

            key = (finding.rule_id or finding.rule_category or "default").lower().strip()
            rate = acceptance_rates.get(key, 1.0)
            is_silenced = key in silenced_set or rate < self.silence_threshold

            if is_silenced:
                result.silenced.append(finding)
                result.silenced_count += 1
                result.audit_log.append(
                    f"SILENCED '{finding.title}' ({key}): "
                    f"rate {rate:.2f} < {self.silence_threshold:.2f}."
                )
                continue

            if rate < self.degrade_threshold:
                original_severity = finding.severity
                finding.severity = Severity.LOW
                result.published.append(finding)
                result.downgraded_count += 1
                result.audit_log.append(
                    f"DOWNGRADED '{finding.title}' "
                    f"({original_severity}->{finding.severity}, {rate:.2f})."
                )
                continue

            result.published.append(finding)
            result.audit_log.append(f"KEPT '{finding.title}': Rate {rate:.2f}.")

        return result
