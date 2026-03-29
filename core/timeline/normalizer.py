"""
core/timeline/normalizer.py
============================
Normalization pipeline for DroidTrace Pro.

Transforms a raw list of ParsedEvents into a clean, consistent list of
NormalizedEvents ready for timeline reconstruction.

Pipeline stages (in order):
─────────────────────────────────────────────────────────────────────────────
  Stage 1 — TIMESTAMP VALIDATION & ISO CONVERSION
      • Ensure every event has a UTC-aware datetime
      • Apply floor (Android epoch: 2008-10-22) and ceiling (collection time + 1d)
      • Emit ISO 8601 UTC string with microsecond precision and Z suffix
      • Flag events whose timestamps were clamped or approximate

  Stage 2 — CLOCK SKEW DETECTION
      • Detect systematic device clock offset against collection time
      • Flag individual events that are ahead of collection time
      • Optionally apply offset correction (disabled by default for forensic
        neutrality — we flag but never silently rewrite evidence timestamps)

  Stage 3 — NOISE REMOVAL
      • Drop known non-user-activity events (CONFIGURATION_CHANGE floods,
        SYSTEM_INTERACTION bursts from OS packages)
      • Preserve HIGH-VALUE system events regardless of source package
        (DEVICE_STARTUP, DEVICE_SHUTDOWN, KEYGUARD_*, USER_UNLOCKED)
      • Rate-limit identical (app, event_type) pairs within a burst window
        and collapse N occurrences into 1 flagged "HIGH_FREQUENCY_COLLAPSED"

  Stage 4 — TEMPORAL DEDUPLICATION
      • Sliding-window deduplication: events sharing (app, event_type) within
        DEDUP_WINDOW_SECONDS of each other are collapsed to the earliest
      • Distinct from parser-level deduplication (which uses exact key hash);
        this catches near-duplicates from overlapping artifact sources

  Stage 5 — FINAL SORT & AUDIT
      • Sort chronologically by timestamp
      • Emit NormalizationReport with per-stage counters

Design decisions:
─────────────────────────────────────────────────────────────────────────────
  • NEVER silently discard data.  Events failing validation are FLAGGED and
    retained; complete removal only happens for confirmed noise categories.
  • ALL thresholds live in NormalizationConfig (or fall back to config/settings.py)
    so nothing is hard-coded.
  • Each stage is a pure transform: (list[Event]) → (list[Event], stats).
    This makes each stage independently testable.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from config.settings import DEDUP_WINDOW_SECONDS
from models.normalized_event import NormalizedEvent
from models.parsed_event import ParsedEvent
from utils.logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

def is_safe_app(app: str) -> bool:
    """True if app is a system package that can be ignored for baseline noise."""
    return any(app.startswith(p) for p in settings.SAFE_PREFIXES)

# The earliest plausible Android device timestamp (for this tool's scope).
# Android OS 1.0 launched 2008-10-22, but we enforce a stricter 2015 limit.
_ANDROID_EPOCH = datetime(2015, 1, 1, tzinfo=timezone.utc)

# How far into the future (beyond collection time) a timestamp may be before
# we consider it a clock skew anomaly.  1 day is generous.
_FUTURE_TOLERANCE = timedelta(days=1)

# ISO 8601 UTC format string — always include microseconds and Z suffix.
_ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

# ── Noise: event types that are always filtered regardless of package ─────────
# These are internal Android bookkeeping events with no user-activity meaning.
_NOISE_EVENT_TYPES: frozenset[str] = frozenset({
    "CONFIGURATION_CHANGE",
    "SYSTEM_INTERACTION",
    "END_OF_DAY",
    "CONTINUE_PREVIOUS_DAY",
    "FLUSH_TO_DISK",
    "STANDBY_BUCKET_CHANGED",
    "SLICE_PINNED",
    "SLICE_PINNED_PRIV",
    "CHOOSER_ACTION",
})

# ── High-value event types: always PRESERVED regardless of source package ─────
# These carry irreplaceable forensic signals for device state reconstruction.
# Phase 3: Strict whitelist for final timeline events.
_HIGH_VALUE_EVENT_TYPES: frozenset[str] = frozenset({
    "APP_INSTALLED",
    "APP_UPDATED",
    "APP_OPENED",
    "APP_SESSION",
    "CORRELATED",
    "ACTIVITY_GAP",
    "SUSPICIOUS"  # Allow internally flagged events
})

# ── Noise: packages whose routine activity is excluded from the timeline ───────
# These generate continuous background events unrelated to user actions.
# Exception: any HIGH_VALUE event type from these packages IS preserved.
from config import settings

def is_safe_app(app: str) -> bool:
    """True if app is a system package that can be ignored for baseline noise."""
    return any(app.startswith(p) for p in settings.SAFE_PREFIXES)

# ── Rate-limit: collapse if same (app, event_type) appears more than N times ──
# within a 60-second window. High-intensity bursts are collapsed to one event.
_BURST_RATE_LIMIT: int = 5          # max occurrences before collapsing
_BURST_WINDOW_SECONDS: int = 60     # sliding window for rate limiting

# ── Source Authority ─────────────────────────────────────────────────────────
# Lower index = higher authority. Matches validator.py.
_SOURCE_PRIORITY = [
    "package_detail",   # Most authoritative: direct PackageManager records.
    "usage_stats",      # Authoritative for user interaction timing.
    "inferred",         # Synthesized by inference engine.
    "app_list",         # Weak: presence-only, no timestamps.
]

def _resolve_source_rank(source: str) -> int:
    """Return the numerical rank (lower is better) of a forensic source."""
    try:
        return _SOURCE_PRIORITY.index(source)
    except ValueError:
        return len(_SOURCE_PRIORITY)


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class NormalizationConfig:
    """
    Configurable thresholds for the normalization pipeline.

    All fields have sensible forensic defaults.  Override specific values
    when creating the normalizer to tune behaviour per investigation.

    Attributes:
        dedup_window_seconds:     Near-duplicate sliding window (Stage 4).
        burst_rate_limit:         Max events before burst collapse (Stage 3).
        burst_window_seconds:     Window for rate-limit counting (Stage 3).
        apply_clock_correction:   If True, shift timestamps by detected offset.
                                  Default False — flag only, never rewrite evidence.
        remove_noise_packages:    If False, keep all packages (for raw audit mode).
        remove_noise_event_types: If False, keep all event types.
        min_timestamp:            Floor timestamp (default: Android epoch).
        max_timestamp_offset:     Ceiling = collection_time + this delta.
    """
    dedup_window_seconds: int = DEDUP_WINDOW_SECONDS
    burst_rate_limit: int = _BURST_RATE_LIMIT
    burst_window_seconds: int = _BURST_WINDOW_SECONDS
    apply_clock_correction: bool = False
    remove_noise_packages: bool = True
    remove_noise_event_types: bool = True
    min_timestamp: datetime = field(default_factory=lambda: _ANDROID_EPOCH)
    max_timestamp_offset: timedelta = field(default_factory=lambda: _FUTURE_TOLERANCE)
    strict_min_year: int = 2015
    strict_max_year: int = 2035


# ─────────────────────────────────────────────────────────────────────────────
# Normalization report
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class NormalizationReport:
    """
    Statistics and audit summary from a single normalizer run.

    Attributes:
        input_count:          Total events received.
        output_count:         Events remaining after normalization.
        removed_noise:        Events dropped as confirmed noise.
        collapsed_bursts:     Individual events merged into burst-collapsed events.
        deduped_temporal:     Events removed by temporal (near-duplicate) dedup.
        timestamp_floored:    Events whose timestamps were raised to min_timestamp.
        timestamp_ceiled:     Events whose timestamps were lowered to collection time.
        approximate_count:    Events carrying the TIMESTAMP_APPROXIMATE flag.
        skew_detected:        Events flagged as ahead of collection time.
        timestamp_invalidated: Events with timestamps outside strict year bounds.
        elapsed_ms:           Total wall-clock time for the normalization run.
    """
    input_count: int = 0
    output_count: int = 0
    removed_noise: int = 0
    collapsed_bursts: int = 0
    deduped_temporal: int = 0
    timestamp_floored: int = 0
    timestamp_ceiled: int = 0
    approximate_count: int = 0
    skew_detected: int = 0
    timestamp_invalidated: int = 0
    elapsed_ms: int = 0

    def summary(self) -> str:
        reduction = self.input_count - self.output_count
        pct = (reduction / self.input_count * 100) if self.input_count else 0
        return (
            f"NormalizationReport: {self.input_count} in -> {self.output_count} out "
            f"({reduction} removed, {pct:.1f}% reduction) | "
            f"noise={self.removed_noise}, bursts={self.collapsed_bursts}, "
            f"dedup={self.deduped_temporal}, skew={self.skew_detected}, "
            f"approx={self.approximate_count}, invalid={self.timestamp_invalidated} | "
            f"{self.elapsed_ms}ms"
        )


# ─────────────────────────────────────────────────────────────────────────────
# EventNormalizer
# ─────────────────────────────────────────────────────────────────────────────

class EventNormalizer:
    """
    Multi-stage normalization pipeline for forensic event streams.

    Usage:
        normalizer = EventNormalizer(collection_time=artifact.collected_at)
        events, report = normalizer.normalize(parsed_events)
        print(report.summary())

    Args:
        collection_time:  UTC datetime when artifacts were collected from the device.
                          Used as the ceiling for timestamp validation and skew detection.
        config:           Optional :class:`NormalizationConfig` to override defaults.
    """

    def __init__(
        self,
        collection_time: datetime,
        config: Optional[NormalizationConfig] = None,
    ) -> None:
        if collection_time.tzinfo is None:
            raise ValueError("collection_time must be timezone-aware (UTC).")
        self._collection_time = collection_time
        self._config = config or NormalizationConfig()
        self._max_timestamp = collection_time + self._config.max_timestamp_offset
        log.debug(
            "EventNormalizer initialised: collection_time=%s, config=%s",
            collection_time.isoformat(), self._config,
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def normalize(
        self,
        events: list[ParsedEvent],
    ) -> tuple[list[NormalizedEvent], NormalizationReport]:
        """
        Run all normalization stages on the input event list.

        Args:
            events: ParsedEvents from ``parse_artifacts()``.

        Returns:
            Tuple of (normalized_events, report).
            ``normalized_events`` is chronologically sorted.
        """
        import time as _time
        t_start = _time.monotonic()
        report = NormalizationReport(input_count=len(events))
        log.info("Normalization starting: %d events", len(events))

        # ── Stage 1: Timestamp validation & ISO conversion ─────────────────
        stage1, s1_stats = self._stage1_timestamps(events)
        report.timestamp_floored  = s1_stats["floored"]
        report.timestamp_ceiled   = s1_stats["ceiled"]
        report.approximate_count  = s1_stats["approximate"]
        report.timestamp_invalidated = s1_stats["invalidated"]
        log.info("Stage 1 (Timestamps) complete: %d events preserved", len(stage1))
        if not stage1 and events:
            log.warning("ALL events dropped in Stage 1! Check timestamp bounds.")

        # ── Stage 2: Clock skew detection ─────────────────────────────────
        stage2, s2_stats = self._stage2_clock_skew(stage1)
        report.skew_detected = s2_stats["skew_flagged"]
        log.debug("Stage 2 complete: skew_flagged=%d", report.skew_detected)

        # ── Stage 3: Noise removal & burst rate-limiting ──────────────────
        stage3, s3_stats = self._stage3_noise(stage2)
        report.removed_noise     = s3_stats["noise_removed"]
        report.collapsed_bursts  = s3_stats["burst_collapsed"]
        log.info(
            "Stage 3 (Noise) complete: %d events remaining (removed %d noise, collapsed %d bursts)",
            len(stage3), report.removed_noise, report.collapsed_bursts,
        )
        if not stage3 and stage2:
            log.warning("ALL events dropped in Stage 3! Noise filters may be too aggressive.")

        # ── Stage 4: Temporal deduplication ───────────────────────────────
        stage4, s4_stats = self._stage4_temporal_dedup(stage3)
        report.deduped_temporal = s4_stats["deduped"]
        log.debug("Stage 4 complete: %d remaining (deduped=%d)", len(stage4), report.deduped_temporal)

        # ── Stage 5: Final sort ────────────────────────────────────────────
        final = sorted(stage4, key=lambda e: (e.timestamp is None, e.timestamp))

        report.output_count = len(final)
        report.elapsed_ms = int((_time.monotonic() - t_start) * 1000)
        log.info(report.summary())

        return final, report

    # ── Stage implementations ─────────────────────────────────────────────────

    def _stage1_timestamps(
        self,
        events: list[ParsedEvent],
    ) -> tuple[list[NormalizedEvent], dict]:
        """
        Stage 1: Validate timestamps, apply bounds, convert to ISO 8601.

        For each event:
          • Ensures the timestamp is UTC-aware.
          • If timestamp < Android epoch (2008-10-22): clamp to Android epoch,
            flag TIMESTAMP_FLOOR_APPLIED. This catches corrupt/zero epoch data.
          • If timestamp > collection_time + tolerance: clamp to collection_time,
            flag TIMESTAMP_CEILING_APPLIED. Avoids impossible future events.
          • Generates iso_timestamp string in format: 2024-01-15T22:30:45.000000Z
          • Propagates timestamp_approximate flag from ParsedEvent.
        """
        result: list[NormalizedEvent] = []
        stats = {"floored": 0, "ceiled": 0, "approximate": 0, "invalidated": 0}

        for event in events:
            flags: list[str] = []
            ts = event.timestamp
            valid_time = True
            iso_ts = "UNKNOWN"

            if ts is None:
                valid_time = False
                flags.append("TIMESTAMP_INVALID")
                stats["invalidated"] += 1
                # Ensure the event carries the valid_time=False flag
            else:
                # Ensure UTC-aware
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                    flags.append("TIMEZONE_ASSUMED_UTC")

                # Strict bounds checking (Forensic rule: 2015 to 2035 by default)
                if ts.year < self._config.strict_min_year or ts.year > self._config.strict_max_year:
                    log.debug("Timestamp %s is out of strict bounds (%d-%d) for %s — invalidating", 
                             ts.isoformat(), self._config.strict_min_year, self._config.strict_max_year, event.app)
                    ts = None
                    valid_time = False
                    flags.append("TIMESTAMP_INVALID")
                    stats["invalidated"] += 1
                else:
                    iso_ts = ts.strftime(_ISO_FORMAT)

            # Propagate approximate flag
            if event.timestamp_approximate and valid_time:
                flags.append("TIMESTAMP_APPROXIMATE")
                stats["approximate"] += 1

            result.append(NormalizedEvent(
                timestamp=ts,
                iso_timestamp=iso_ts,
                valid_time=valid_time,
                app=_clean_package_name(event.app),
                event_type=event.event_type.strip().upper(),
                source=event.source,
                evidence_type=event.evidence_type,
                raw_fields=event.raw_fields,
                source_command=event.source_command,
                timestamp_approximate=event.timestamp_approximate,
                dedup_key=event.dedup_key,
                normalization_flags=flags,
            ))

        return result, stats

    def _stage2_clock_skew(
        self,
        events: list[NormalizedEvent],
    ) -> tuple[list[NormalizedEvent], dict]:
        """
        Stage 2: Detect device clock skew.

        Clock skew detection:
          • An event is CLOCK_SKEW_DETECTED if its timestamp is more than
            60 seconds ahead of the collection time, but still within the
            1-day tolerance window (those were already clamped in Stage 1).
          • This typically means the device's clock was set ahead of the
            forensic workstation at collection time — a potential anti-forensic
            indicator or simply a misconfigured device clock.

        If config.apply_clock_correction is True:
          • Compute the median skew across all skewed events.
          • Subtract the skew from all event timestamps (and regenerate iso_ts).
          • Flag as CLOCK_SKEW_CORRECTED.

        Forensic policy (default):
          apply_clock_correction = False — we FLAG, never silently rewrite
          timestamps.  The investigator decides whether to apply correction.
        """
        SKEW_THRESHOLD = timedelta(seconds=60)
        stats = {"skew_flagged": 0}

        # First pass: collect dedup_keys and deltas for skewed events
        skewed_keys: set[str] = set()
        skew_deltas: list[float] = []
        for event in events:
            if not event.valid_time or event.timestamp is None:
                continue
                
            if event.timestamp > self._collection_time + SKEW_THRESHOLD:
                skewed_keys.add(event.dedup_key)
                skew_deltas.append(
                    (event.timestamp - self._collection_time).total_seconds()
                )

        stats["skew_flagged"] = len(skewed_keys)
        if skewed_keys:
            log.warning(
                "Clock skew detected: %d events ahead of collection time "
                "(median offset: +%.1fs)",
                len(skewed_keys), _median(skew_deltas),
            )

        # Second pass: annotate (and optionally correct) events
        result: list[NormalizedEvent] = []
        for event in events:
            flags = list(event.normalization_flags)
            ts = event.timestamp

            if event.dedup_key in skewed_keys and event.valid_time and ts is not None:
                flags.append("CLOCK_SKEW_DETECTED")
                if self._config.apply_clock_correction and skew_deltas:
                    correction = timedelta(seconds=_median(skew_deltas))
                    ts = ts - correction
                    flags.append("CLOCK_SKEW_CORRECTED")

            result.append(_replace_event(event, timestamp=ts, flags=flags))

        return result, stats


    def _stage3_noise(
        self,
        events: list[NormalizedEvent],
    ) -> tuple[list[NormalizedEvent], dict]:
        """
        Stage 3: Noise removal and burst rate-limiting.

        Noise removal rules (applied in order — first match wins):

        Rule 3a — HIGH VALUE PRESERVE:
          If event_type is in _HIGH_VALUE_EVENT_TYPES → always keep.
          This rule overrides all subsequent rules.

        Rule 3b — NOISE EVENT TYPE:
          If event_type is in _NOISE_EVENT_TYPES → drop.
          These are internal Android administrative events.

        Rule 3c — NOISE PACKAGE:
          If app is in _NOISE_PACKAGES AND event_type is NOT high-value → drop.
          System processes generate thousands of events; only their
          significant lifecycle events (handled by 3a) are relevant.

        Rule 3d — BURST RATE LIMIT:
          If the same (app, event_type) appears more than BURST_RATE_LIMIT
          times within BURST_WINDOW_SECONDS:
            • Keep the first occurrence.
            • Collapse all subsequent occurrences into a single
              HIGH_FREQUENCY_COLLAPSED event attached to the first.
            • Flag the kept event as "HIGH_FREQUENCY_COLLAPSED".
          This eliminates notification-spam and sensor-polling floods.
        """
        if not self._config.remove_noise_packages and not self._config.remove_noise_event_types:
            return events, {"noise_removed": 0, "burst_collapsed": 0}

        stats = {"noise_removed": 0, "burst_collapsed": 0}

        # ── Rules 3a/3b/3c ────────────────────────────────────────────────
        after_static: list[NormalizedEvent] = []
        for event in events:
            # Phase 3: Remove APP_LISTED noise
            if event.event_type == "APP_LISTED":
                stats["noise_removed"] += 1
                continue

            # Rule 3a: Always preserve high-value types defined in Phase 3
            if event.event_type in _HIGH_VALUE_EVENT_TYPES:
                after_static.append(event)
                continue

            # Phase 3: Completely remove APP_LISTED noise
            if event.event_type == "APP_LISTED":
                stats["noise_removed"] += 1
                continue

            # Phase 3: Strict Whitelist - drop DIRECT events not in allowed list
            # if event.event_type not in _HIGH_VALUE_EVENT_TYPES and event.evidence_type == "DIRECT":
            #     stats["noise_removed"] += 1
            #     continue

            # Phase 4/5: Filter by SAFE_PREFIXES (Ignore system/vendor apps)
            # Exception: Usage sessions (APP_OPENED/SESSION) or Correlated events are kept.
            if any(event.app.startswith(p) for p in _SAFE_PREFIXES):
                if event.event_type not in ("APP_OPENED", "APP_SESSION", "ACTIVITY_GAP", "CORRELATED"):
                    stats["noise_removed"] += 1
                    continue

            after_static.append(event)

        # ── Rule 3d: Burst rate-limiting ───────────────────────────────────
        # Pre-sort so the burst window scans chronologically
        after_static.sort(key=lambda e: (e.timestamp is None, e.timestamp))

        # Track: (app, event_type) → list of timestamps seen in current window
        window: dict[tuple[str, str], list[datetime]] = defaultdict(list)  # type: ignore[assignment]
        after_burst: list[NormalizedEvent] = []

        w_seconds = timedelta(seconds=self._config.burst_window_seconds)
        limit = self._config.burst_rate_limit

        for event in after_static:
            if event.timestamp is None:
                # Events without timestamps cannot be burst-collapsed; 
                # keep them but don't track them in the sliding window.
                after_burst.append(event)
                continue
                
            key = (event.app, event.event_type)
            # Expire entries outside the window
            cutoff = event.timestamp - w_seconds
            window[key] = [t for t in window[key] if t >= cutoff]

            count_in_window = len(window[key])

            if count_in_window < limit:
                # Under rate limit — keep event normally
                window[key].append(event.timestamp)
                after_burst.append(event)
            else:
                # Over rate limit — collapse: first event kept, rest dropped
                if count_in_window == limit:
                    # This is exactly the Nth occurrence — flag the (limit)th event
                    # as the start of a collapse and keep it
                    flags = list(event.normalization_flags) + ["HIGH_FREQUENCY_COLLAPSED"]
                    after_burst.append(_replace_event(event, flags=flags))
                    window[key].append(event.timestamp)
                else:
                    # Past the limit — silently drop (collapse already applied above)
                    stats["burst_collapsed"] += 1
                    log.debug(
                        "Burst collapsed: %s/%s (count=%d in %ds window)",
                        event.app, event.event_type, count_in_window + 1,
                        self._config.burst_window_seconds,
                    )

        return after_burst, stats

    def _stage4_temporal_dedup(
        self,
        events: list[NormalizedEvent],
    ) -> tuple[list[NormalizedEvent], dict]:
        """
        Stage 4: Temporal (sliding-window) near-duplicate removal.

        Algorithm:
          Pre-sort events chronologically.
          For each (app, event_type) pair, identify clusters within window.
          In each cluster, keep ONLY the event from the most authoritative source.
        """
        if not events:
            return [], {"deduped": 0}

        events_sorted = sorted(events, key=lambda e: (e.timestamp is None, e.timestamp))
        window_delta = timedelta(seconds=self._config.dedup_window_seconds)
        stats = {"deduped": 0}

        result: list[NormalizedEvent] = []
        
        # We process in windows. This is slightly more complex than the previous
        # implementation because we need to look ahead to find the "best" source
        # in a cluster of near-simultaneous events.
        
        i = 0
        n = len(events_sorted)
        while i < n:
            current = events_sorted[i]
            cluster = [current]
            
            # Find all subsequent events for the same (app, type) within window
            j = i + 1
            while j < n:
                candidate = events_sorted[j]
                
                # If either has no valid time, they cannot be part of the temporal deduplication window
                if not candidate.valid_time or not current.valid_time or candidate.timestamp is None or current.timestamp is None:
                    break
                    
                if (candidate.timestamp - current.timestamp) > window_delta:
                    break
                if candidate.app == current.app and candidate.event_type == current.event_type:
                    cluster.append(candidate)
                j += 1
            
            if len(cluster) == 1:
                result.append(current)
                i += 1
            else:
                # Resolve by source priority
                best_event = min(cluster, key=lambda e: _resolve_source_rank(e.source))
                result.append(best_event)
                stats["deduped"] += len(cluster) - 1
                log.debug(
                    "Temporal Priority Dedup: kept %s from %s, dropped %d subordinates",
                    current.app, best_event.source, len(cluster) - 1
                )
                # Skip the rest of the cluster members
                # For simplicity in this sliding window, we'll just increment i
                # based on how many cluster members we skipped.
                # However, some cluster members might have been "potential starts" 
                # for other windows. We'll just advance to the end of the cluster.
                i = j

        return result, stats


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def normalize_events(
    events: list[ParsedEvent],
    collection_time: datetime,
    config: Optional[NormalizationConfig] = None,
) -> tuple[list[NormalizedEvent], NormalizationReport]:
    """
    Normalize a list of ParsedEvents into clean, consistent NormalizedEvents.

    This is the primary public entry point for the normalization layer.
    Called by the timeline builder immediately after ``parse_artifacts()``.

    Args:
        events:          Raw ParsedEvent list from ``parse_artifacts()``.
        collection_time: UTC datetime marking when collection began.
                         Used as the upper timestamp bound and skew reference.
        config:          Optional configuration overrides.

    Returns:
        Tuple of (normalized_events, report).
        ``normalized_events`` is chronologically sorted and ready for the
        timeline reconstruction engine.

    Example:
        from core.parsers.parser import parse_artifacts
        from core.timeline.normalizer import normalize_events

        parsed  = parse_artifacts(raw_artifacts)
        clean,  report = normalize_events(parsed, collection_time=collected_at)
        print(report.summary())
    """
    normalizer = EventNormalizer(collection_time=collection_time, config=config)
    return normalizer.normalize(events)


# ─────────────────────────────────────────────────────────────────────────────
# Pure helper functions
# ─────────────────────────────────────────────────────────────────────────────

def _clean_package_name(pkg: str) -> str:
    """
    Sanitise a package name string for consistent downstream use.

    Operations:
      - Strip surrounding whitespace
      - Lowercase (package names are case-insensitive in practice)
      - Remove any null bytes or control characters that could
        corrupt database fields or report output

    Args:
        pkg: Raw package name string from ADB output.

    Returns:
        Cleaned package name string. Empty string if input is None/empty.
    """
    if not pkg:
        return ""
    # Remove control characters (non-printable ASCII)
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", pkg)
    return cleaned.strip().lower()


def _replace_event(
    event: NormalizedEvent,
    timestamp: Optional[datetime] = None,
    flags: Optional[list[str]] = None,
) -> NormalizedEvent:
    """
    Return a new NormalizedEvent with selected fields replaced.
    NormalizedEvent is a regular (mutable) dataclass so we can assign fields,
    but we use a copy-and-replace pattern to keep each stage's output immutable
    relative to its input.

    Args:
        event:     The source event to copy.
        timestamp: If provided, replaces the timestamp AND regenerates iso_timestamp.
        flags:     If provided, replaces normalization_flags entirely.

    Returns:
        A new NormalizedEvent instance.
    """
    ts = timestamp if timestamp is not None else getattr(event, 'timestamp', None)
    
    if ts is None:
        iso_ts = getattr(event, 'iso_timestamp', "UNKNOWN")
    else:
        iso_ts = ts.strftime(_ISO_FORMAT) if timestamp is not None else event.iso_timestamp

    return NormalizedEvent(
        timestamp=ts,
        iso_timestamp=iso_ts,
        valid_time=getattr(event, 'valid_time', True),
        app=event.app,
        event_type=event.event_type,
        source=event.source,
        evidence_type=event.evidence_type,
        raw_fields=event.raw_fields,
        source_command=event.source_command,
        timestamp_approximate=event.timestamp_approximate,
        dedup_key=event.dedup_key,
        normalization_flags=flags if flags is not None else list(event.normalization_flags),
    )


def _median(values: list[float]) -> float:
    """
    Compute the median of a non-empty list of floats.
    Uses a sort-based approach — suitable for the small lists produced
    during skew detection (typically < 100 events).

    Args:
        values: List of numeric values.

    Returns:
        Median value.  Returns 0.0 for empty input.
    """
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    if len(s) % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0
