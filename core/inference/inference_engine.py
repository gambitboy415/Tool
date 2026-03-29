"""
core/inference/inference_engine.py
=====================================
Inference Engine for DroidTrace Pro.

Responsibility:
    Run all registered InferenceRules against the post-correlation timeline.
    Collect newly synthesised INFERRED events, merge them back into the timeline,
    re-sort chronologically, and re-assign sequence indices.

Design:
    - Runs AFTER the CorrelationEngine has finished.
    - Additive only: flags are added, INFERRED events are appended, nothing removed.
    - Rules are applied in order; later rules see flags set by earlier rules,
      enabling compound detection (e.g. AntiForensicSequence can see flags
      placed by RapidInstallUninstall).
    - Crash isolation: one rule failing never stops the rest.
    - Final step re-indexes the entire timeline (sequence_index) so the GUI
      always sees a correct, gapless sequence.

Usage:
    engine = InferenceEngine()
    report = engine.run(timeline)
    # timeline is now mutated + extended with INFERRED events
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.inference.rules.base_inference import InferenceRule
from core.inference.rules.behavioral_rules import (
    ActivityGapRule,
    AppCamouflageRule,
    TimestampIntegrityRule,
    LateNightActivityRule,
    ImmediateAppUseRule,
    CommunicationBurstRule,
    SilentServiceRule,
    RapidInstallUninstallRule,
    AntiForensicSequenceRule,
    FactoryResetIndicatorRule,
)
from core.inference.rules.fusion_rules import (
    AppLifecycleFusionRule,
    SuspiciousRemovalFusionRule,
    DormantAppFusionRule,
    HeavyUsageFusionRule,
    BackgroundActivityFusionRule,
)
from models.timeline_event import TimelineEvent
from utils.logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Inference report
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InferenceReport:
    """
    Summary of inference engine results.

    Attributes:
        total_events_in:    Events in the timeline at engine entry.
        total_events_out:   Events in the timeline after engine (includes INFERRED).
        inferred_added:     New INFERRED events synthesised and merged.
        flags_attached:     Total flag strings added to existing events.
        flagged_events:     Distinct events that received at least one flag.
        rules_applied:      Names of rules that ran.
        rule_inferred:      Per-rule count of new INFERRED events created.
        rule_flagged:       Per-rule count of flag additions per rule.
        suspicious_apps:    Distinct app package names that carry at least one flag.
        elapsed_ms:         Wall-clock time for the run.
    total_active_time:  Sum of durations of all APP_SESSION events (seconds).
    app_usage_breakdown: Mapping of app -> total_seconds_used.
    """
    total_events_in: int = 0
    total_events_out: int = 0
    inferred_added: int = 0
    flags_attached: int = 0
    flagged_events: int = 0
    rules_applied: list[str] = field(default_factory=list)
    rule_inferred: dict[str, int] = field(default_factory=dict)
    rule_flagged: dict[str, int] = field(default_factory=dict)
    suspicious_apps: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
    total_active_time: float = 0.0
    app_usage_breakdown: dict[str, float] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"InferenceReport: {self.total_events_in} in -> {self.total_events_out} out | "
            f"inferred={self.inferred_added}, flags={self.flags_attached}, "
            f"flagged_events={self.flagged_events}, "
            f"active_time={self.total_active_time/60:.1f}m | "
            f"suspicious_apps={len(self.suspicious_apps)} | "
            f"{self.elapsed_ms}ms"
        )


# ─────────────────────────────────────────────────────────────────────────────
# InferenceEngine
# ─────────────────────────────────────────────────────────────────────────────

class InferenceEngine:
    """
    Orchestrates all inference rules against the correlated timeline.

    Default rule execution order:
      1.  LateNightActivityRule        (flagging — sets LATE_NIGHT_ACTIVITY)
      2.  ImmediateAppUseRule          (flagging — sets IMMEDIATE_APP_USE)
      3.  RapidInstallUninstallRule    (flagging — sets RAPID_INSTALL_UNINSTALL)
      4.  AppCamouflageRule            (flagging — sets APP_CAMOUFLAGE_SUSPECTED)
      5.  SilentServiceRule            (flagging — sets SILENT_BACKGROUND_SERVICE)
      6.  CommunicationBurstRule       (flagging — sets COMMUNICATION_BURST)
      7.  ActivityBlackoutRule         (synthesis — creates ACTIVITY_BLACKOUT events)
      8.  FactoryResetIndicatorRule    (synthesis — creates FACTORY_RESET_INDICATOR)
      9.  DataExfiltrationWindowRule   (synthesis — creates DATA_EXFILTRATION_WINDOW)
      10. AntiForensicSequenceRule     (synthesis — creates ANTI_FORENSIC_SEQUENCE)
      11. TimestampIntegrityRule       (flagging — sets UNCERTAIN_TEMPORAL_ORDER)

    Ordering rationale:
      Flag-attachment rules run first so synthesis rules can optionally check
      flags when building compound detections (e.g. AntiForensicSequence
      can detect RAPID_INSTALL_UNINSTALL flags placed by rule 3).

    Args:
        extra_rules: Additional InferenceRule instances appended to the default set.
    """

    _DEFAULT_RULES: list[type[InferenceRule]] = []

    def __init__(self, extra_rules: list[InferenceRule] | None = None) -> None:
        self._rules: list[InferenceRule] = [cls() for cls in self._DEFAULT_RULES]
        if extra_rules:
            self._rules.extend(extra_rules)
        log.debug(
            "InferenceEngine initialised with %d rules: %s",
            len(self._rules), [r.NAME for r in self._rules],
        )

    def run(self, timeline: list[TimelineEvent]) -> InferenceReport:
        """
        Apply all inference rules to the timeline.

        Steps:
          1. Apply each rule — flags are set in-place; new INFERRED events collected.
          2. Merge synthesised INFERRED events into the timeline.
          3. Re-sort chronologically.
          4. Re-assign sequence_index (0-based) to the full merged timeline.
          5. Build and return the InferenceReport.

        The ``timeline`` list is mutated AND extended in-place.

        Args:
            timeline: Full TimelineEvent list (post-correlation).

        Returns:
            :class:`InferenceReport` with per-rule statistics.
        """
        import time as _time
        t_start = _time.monotonic()
        report = InferenceReport(total_events_in=len(timeline))

        # Snapshot flag counts before run for delta calculation
        flags_before = sum(len(e.flags) for e in timeline)

        all_new_events: list[TimelineEvent] = []

        for rule in self._rules:
            try:
                flags_before_rule = sum(len(e.flags) for e in timeline)
                new_events = rule.apply(timeline)

                # Count flags added by this rule
                flags_after_rule = sum(len(e.flags) for e in timeline)
                rule_flag_delta = flags_after_rule - flags_before_rule

                report.rules_applied.append(rule.NAME)
                report.rule_inferred[rule.NAME] = len(new_events)
                report.rule_flagged[rule.NAME] = rule_flag_delta

                if new_events:
                    log.info(
                        "Rule '%s' synthesised %d INFERRED event(s)",
                        rule.NAME, len(new_events),
                    )
                    all_new_events.extend(new_events)

                if rule_flag_delta:
                    log.info(
                        "Rule '%s' attached %d flag(s)",
                        rule.NAME, rule_flag_delta,
                    )

            except Exception as exc:  # noqa: BLE001
                log.error(
                    "Inference rule '%s' raised an exception: %s",
                    rule.NAME, exc, exc_info=True,
                )

        # ── Merge INFERRED events into the timeline ────────────────────────
        timeline.extend(all_new_events)

        # ── Re-sort chronologically ────────────────────────────────────────
        timeline.sort(key=lambda e: (e.timestamp is None, e.timestamp))

        # ── Re-assign stable sequence indices ─────────────────────────────
        for idx, event in enumerate(timeline):
            event.sequence_index = idx

        # ── Build report ───────────────────────────────────────────────────
        flags_after = sum(len(e.flags) for e in timeline)
        report.inferred_added   = len(all_new_events)
        report.flags_attached   = flags_after - flags_before
        report.flagged_events   = sum(1 for e in timeline if e.flags)
        report.total_events_out = len(timeline)
        report.suspicious_apps  = sorted({
            e.app for e in timeline if e.flags and e.app != "system"
        })
        
        # ── Calculate Global Metrics ───────────────────────────────────────
        for e in timeline:
            if e.event_type == "APP_SESSION":
                duration = e.raw_fields.get("duration_sec", 0.0)
                report.total_active_time += duration
                if e.app:
                    report.app_usage_breakdown[e.app] = report.app_usage_breakdown.get(e.app, 0.0) + duration

        report.elapsed_ms = int((_time.monotonic() - t_start) * 1000)

        log.info(report.summary())
        return report
