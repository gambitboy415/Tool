"""
core/timeline/timeline_builder.py
===================================
Timeline Reconstruction Engine for DroidTrace Pro.

Responsibility:
    Accept a chronologically-sorted list of NormalizedEvents (from the
    normalization layer) and convert each one into a TimelineEvent, assigning
    stable sequence indices and generating human-readable descriptions.

    The output of this module is a flat, ordered list[TimelineEvent] where
    every event is classified DIRECT.  Subsequent engines (Correlation,
    Inference) upgrade classifications and attach flags — this module is
    deliberately simple and stateless.

Design:
    - Pure transform: NormalizedEvent → TimelineEvent (1-to-1 mapping).
    - No classification upgrades here — that is the correlation engine's job.
    - Description generation is rule-based per event_type; unknown types get
      a generic fallback so no event is left without a human-readable label.
    - The builder accepts events from multiple artifact sources and produces
      a single unified, indexed timeline.
"""

from __future__ import annotations

from datetime import datetime, timezone

from models.normalized_event import NormalizedEvent
from models.timeline_event import TimelineEvent
from utils.logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Description templates
# Maps event_type → description template string.
# Supports one optional format argument: {app}
# ─────────────────────────────────────────────────────────────────────────────

_DESCRIPTIONS: dict[str, str] = {
    # App lifecycle
    "APP_OPENED":                "{app} was opened (moved to foreground)",
    "APP_CLOSED":                "{app} was closed (moved to background)",
    "ACTIVITY_RESUMED":          "{app} activity resumed by user",
    "ACTIVITY_PAUSED":           "{app} activity paused",
    "ACTIVITY_STOPPED":          "{app} activity stopped",
    "ACTIVITY_DESTROYED":        "{app} activity destroyed",
    "APP_LISTED":                "{app} found in installed package inventory",
    "APP_UNINSTALL_RESIDUAL":    "{app} uninstalled but data residue remains on device",

    # Install / update / removal
    "APP_INSTALLED":             "{app} was installed on the device",
    "APP_UPDATED":               "{app} was updated to a new version",
    "APP_UNINSTALLED":           "{app} was uninstalled from the device",

    # Services
    "FOREGROUND_SERVICE_START":  "{app} started a foreground service (visible notification)",
    "FOREGROUND_SERVICE_STOP":   "{app} stopped its foreground service",

    # Device state
    "DEVICE_STARTUP":            "Device booted up (system restart recorded)",
    "DEVICE_SHUTDOWN":           "Device shut down (intentional or forced)",
    "KEYGUARD_SHOWN":            "Screen lock (keyguard) activated — device locked",
    "KEYGUARD_HIDDEN":           "Screen lock dismissed — device unlocked by user",
    "USER_UNLOCKED":             "User account unlocked (post-boot credential entry)",
    "USER_STOPPED":              "User account stopped (multi-user session ended)",

    # Notifications
    "NOTIFICATION_SEEN":         "{app} notification viewed by user",
    "NOTIFICATION_INTERRUPTION": "{app} sent an interrupting notification",

    # User actions
    "USER_INTERACTION":          "User interacted with {app}",
    "SHORTCUT_INVOCATION":       "App shortcut for {app} was invoked",

    # Usage stats internal
    "USAGE_EVENT":               "Usage stats event recorded for {app}",
}

_GENERIC_DESCRIPTION = "Event '{event_type}' recorded for {app}"


# ─────────────────────────────────────────────────────────────────────────────
# TimelineBuilder
# ─────────────────────────────────────────────────────────────────────────────

class TimelineBuilder:
    """
    Converts a list of NormalizedEvents into a unified, indexed timeline.

    Args:
        events: Chronologically sorted NormalizedEvents from normalize_events().

    Example:
        builder = TimelineBuilder(normalized_events)
        timeline = builder.build()
    """

    def __init__(self, events: list[NormalizedEvent]) -> None:
        self._events = events

    def build(self) -> list[TimelineEvent]:
        """
        Build and return the unified timeline.

        Returns:
            list[TimelineEvent] sorted chronologically, with stable
            zero-based sequence_index values assigned.
        """
        log.info("Building timeline from %d normalised events …", len(self._events))
        timeline: list[TimelineEvent] = []

        # Enforce strict sorting: UNKNOWN timestamps are pushed to the bottom
        sorted_events = sorted(self._events, key=lambda e: (e.timestamp is None, e.timestamp))

        for idx, norm_event in enumerate(sorted_events, start=1):
            te = _to_timeline_event(norm_event, sequence_index=idx)
            timeline.append(te)

        log.info("Timeline built: %d events", len(timeline))
        return timeline


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def build_timeline(normalized_events: list[NormalizedEvent]) -> list[TimelineEvent]:
    """
    Build a unified forensic timeline from normalised events.

    Entry point for the timeline reconstruction layer.

    Args:
        normalized_events: Output of ``normalize_events()`` — must be
                           chronologically sorted.

    Returns:
        Indexed list[TimelineEvent], all classified DIRECT, with mandatory
        integrity flagging applied.
    """
    builder = TimelineBuilder(normalized_events)
    timeline = builder.build()

    # ── STEP 2: Apply Integrity Flagging (Mandatory) ───────────────────────────
    for event in timeline:
        _apply_integrity_flags(event)

    # ── PHASE 6: Post-Build Validation ────────────────────────────────────────
    # Count invalid timestamps separately; verify monotonicity for valid events.
    valid_events = [e for e in timeline if e.valid_time and e.timestamp is not None]
    invalid_count = len(timeline) - len(valid_events)

    if invalid_count:
        log.info(
            "Timeline integrity: %d event(s) have no valid timestamp "
            "(flagged TEMPORAL_INTEGRITY_INVALID, placed at end of timeline)",
            invalid_count,
        )

    # Verify strictly increasing order for timed events
    violations = 0
    prev_ts = None
    for event in valid_events:
        if prev_ts is not None and event.timestamp < prev_ts:
            violations += 1
            log.warning(
                "Chronological violation: [%s] %s/%s appears before previous timestamp %s",
                event.iso_timestamp, event.app, event.event_type, prev_ts.isoformat(),
            )
        prev_ts = event.timestamp

    if violations == 0:
        log.info("Timeline validation PASSED: %d timed events in strict chronological order.", len(valid_events))
    else:
        log.warning("Timeline validation: %d chronological violation(s) detected.", violations)

    return timeline


# ─────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────────────────────

def _apply_integrity_flags(event: TimelineEvent) -> TimelineEvent:
    """
    🟢 STEP 1 — Add Integrity Flagging
    Checks if an event has a valid source timestamp.
    """
    if not event.valid_time:
        event.add_flag("TEMPORAL_INTEGRITY_INVALID")
    return event


def _to_timeline_event(event: NormalizedEvent, sequence_index: int) -> TimelineEvent:
    """
    Convert a single NormalizedEvent into a TimelineEvent.

    All fields are copied verbatim.  The description is generated from the
    event_type template table; unknown types receive a generic fallback.

    Args:
        event:          Source NormalizedEvent.
        sequence_index: Zero-based position in the final timeline.

    Returns:
        A new TimelineEvent classified as DIRECT.
    """
    description = _build_description(event.event_type, event.app)

    return TimelineEvent(
        # Identity
        sequence_index=sequence_index,
        # Temporal
        timestamp=event.timestamp,
        iso_timestamp=event.iso_timestamp,
        valid_time=event.valid_time,
        # Content
        app=event.app,
        event_type=event.event_type,
        source=event.source,
        evidence_type="DIRECT",     # upgraded later by correlation/inference engines
        description=description,
        reason="Raw extraction" if event.valid_time else "Timestamp unavailable or invalid in source data",
        # Correlation placeholders (populated by CorrelationEngine)
        correlation_id=None,
        correlated_with=[],
        inferred_from=[],
        # Behavioral flags (populated by InferenceEngine)
        flags=[],
        # Provenance
        raw_fields=dict(event.raw_fields),
        source_command=event.source_command,
        normalization_flags=list(event.normalization_flags),
        timestamp_approximate=event.timestamp_approximate,
        dedup_key=event.dedup_key,
    )


def _build_description(event_type: str, app: str) -> str:
    """
    Generate a human-readable description for an event.

    Args:
        event_type: Normalised event type string.
        app:        Package name.

    Returns:
        Formatted description string.
    """
    template = _DESCRIPTIONS.get(event_type)
    if template:
        return template.format(app=app, event_type=event_type)

    # Fallback for unknown/dynamic event types (e.g. USAGE_EVENT_42)
    if event_type.startswith("USAGE_EVENT_"):
        code = event_type.replace("USAGE_EVENT_", "")
        return f"Usage stats event code {code} recorded for {app}"

    return _GENERIC_DESCRIPTION.format(app=app, event_type=event_type)
