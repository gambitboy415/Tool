"""
core/correlation/correlation_engine.py
========================================
Event Correlation Engine for DroidTrace Pro.

Responsibility:
    Scan the full DIRECT timeline and apply all registered CorrelationRules.
    Each rule mutates matching TimelineEvents in-place — upgrading their
    evidence_type to CORRELATED and linking them via shared correlation_id.

Architecture:
    The engine follows a Chain-of-Responsibility pattern.  Rules are applied
    in registration order.  Later rules can see the results of earlier rules
    (e.g. an install event already marked CORRELATED by InstallToForeground
    can still be processed by another rule that links it to a network event).

    This additive, non-destructive design preserves the full forensic record.

Usage:
    engine = CorrelationEngine()
    report = engine.run(timeline)
    # timeline events are now mutated in-place (CORRELATED where applicable)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.correlation.rules.base_rule import CorrelationRule
from core.correlation.rules.app_lifecycle_rules import (
    AppLifecycleRule,
    SuspiciousRemovalRule,
    DormantAppRule,
    BackgroundActivityRule,
)
from core.correlation.rules.network_rules import (
    NetworkToggleBeforeActivityRule,
    NetworkAfterUnlockRule,
)
from models.timeline_event import TimelineEvent
from utils.logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Correlation report
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CorrelationReport:
    """
    Summary of correlation engine results.

    Attributes:
        total_events:       Events in the timeline at engine entry.
        correlated_events:  Events promoted to CORRELATED.
        groups_created:     Distinct correlation groups (shared correlation_id).
        rules_applied:      Names of rules that ran.
        rule_hits:          Per-rule count of groups created.
        elapsed_ms:         Wall-clock time for the run.
    """
    total_events: int = 0
    correlated_events: int = 0
    groups_created: int = 0
    rules_applied: list[str] = field(default_factory=list)
    rule_hits: dict[str, int] = field(default_factory=dict)
    elapsed_ms: int = 0

    def summary(self) -> str:
        return (
            f"CorrelationReport: {self.total_events} events | "
            f"{self.correlated_events} correlated across {self.groups_created} groups | "
            f"rules={self.rules_applied} | {self.elapsed_ms}ms"
        )


# ─────────────────────────────────────────────────────────────────────────────
# CorrelationEngine
# ─────────────────────────────────────────────────────────────────────────────

class CorrelationEngine:
    """
    Orchestrates all correlation rules against the forensic timeline.

    Default rule set (applied in order):
      1. AppLifecycleRule      — Links install events to first use
      2. SuspiciousRemovalRule — Links usage sessions to uninstalls
      3. DormantAppRule        — Flags installed apps with no sessions
      4. BackgroundActivityRule — Flags activity while screen is OFF
      5. NetworkToggleBeforeActivityRule — Links network disconnects to user intent
      6. NetworkAfterUnlockRule   — Links unlocking to immediate network access

    Custom rules can be injected via ``extra_rules`` for extensibility.

    Args:
        extra_rules: Additional CorrelationRule instances appended to the
                     default rule set.
    """

    _DEFAULT_RULES: list[type[CorrelationRule]] = [
        AppLifecycleRule,
        SuspiciousRemovalRule,
        DormantAppRule,
        BackgroundActivityRule,
        NetworkToggleBeforeActivityRule,
        NetworkAfterUnlockRule,
    ]

    def __init__(self, extra_rules: list[CorrelationRule] | None = None) -> None:
        self._rules: list[CorrelationRule] = [cls() for cls in self._DEFAULT_RULES]
        if extra_rules:
            self._rules.extend(extra_rules)
        log.debug(
            "CorrelationEngine initialised with %d rules: %s",
            len(self._rules), [r.NAME for r in self._rules],
        )

    def run(self, timeline: list[TimelineEvent]) -> CorrelationReport:
        """
        Apply all correlation rules to the timeline.

        The timeline is mutated in-place.  Events promoted to CORRELATED
        have their ``evidence_type``, ``correlation_id``, and
        ``correlated_with`` fields updated by the rules.

        Args:
            timeline: Full TimelineEvent list from the TimelineBuilder.

        Returns:
            :class:`CorrelationReport` with statistics for each rule.
        """
        import time as _time
        t_start = _time.monotonic()
        report = CorrelationReport(total_events=len(timeline))

        correlated_before = sum(1 for e in timeline if e.evidence_type == "CORRELATED")

        # Exclude events with UNKNOWN timestamps from correlation to prevent math crashes 
        # and false logical adjacency. The original objects are modified by reference.
        valid_timeline = [e for e in timeline if e.valid_time and e.timestamp is not None]

        for rule in self._rules:
            try:
                hits = rule.apply(valid_timeline)
                report.rules_applied.append(rule.NAME)
                report.rule_hits[rule.NAME] = hits
                report.groups_created += hits
                if hits:
                    log.info("Rule '%s' created %d correlation group(s)", rule.NAME, hits)
                else:
                    log.debug("Rule '%s': no matches", rule.NAME)
            except Exception as exc:  # noqa: BLE001
                # A rule crash must never abort the pipeline.
                log.error(
                    "Correlation rule '%s' raised an exception: %s",
                    rule.NAME, exc, exc_info=True,
                )

        correlated_after = sum(1 for e in timeline if e.evidence_type == "CORRELATED")
        report.correlated_events = correlated_after - correlated_before
        report.elapsed_ms = int((_time.monotonic() - t_start) * 1000)
        log.info(report.summary())

        return report
