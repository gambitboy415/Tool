"""
core/timeline/validator.py
==========================
Forensic integrity validation and conflict resolution for DroidTrace Pro.

Responsibilities:
- Enforce logical order: APP_INSTALLED must precede usage (APP_OPENED, etc.).
- Prevent temporal paradoxes: No usage events after APP_UNINSTALLED.
- Conflict resolution: Identify and prune weaker conflicting events.
- Audit logging: Record all corrective actions for forensic audit trails.
"""

from __future__ import annotations

from typing import Iterable, Optional
from models.timeline_event import TimelineEvent
from utils.logger import get_logger

log = get_logger(__name__)

# Lower index = higher authority. Used to resolve overlapping/conflicting data.
_SOURCE_PRIORITY = [
    "package_detail",   # Most authoritative: direct PackageManager records.
    "usage_stats",      # Authoritative for user interaction timing.
    "inferred",         # Synthesized by inference engine.
    "app_list",         # Weak: presence-only, no timestamps.
]


def resolve_source_rank(source: str) -> int:
    """Return the numerical rank (lower is better) of a forensic source."""
    try:
        return _SOURCE_PRIORITY.index(source)
    except ValueError:
        return len(_SOURCE_PRIORITY)


class TimelineValidator:
    """
    Validates and repairs a reconstructed timeline to ensure forensic integrity.

    Args:
        log_corrections: If True, all repairs are logged at INFO level.
    """

    def __init__(self, log_corrections: bool = True) -> None:
        self._log_corrections = log_corrections
        self._repairs: list[str] = []

    def validate_and_repair(self, events: Iterable[TimelineEvent]) -> list[TimelineEvent]:
        """
        Main entry point for the validation stage.
        Runs the full check-and-repair suite return a corrected timeline.
        """
        # Sort by timestamp first to ensure sequential checks work
        sorted_events = sorted(events, key=lambda e: (e.timestamp is None, e.timestamp))
        
        # 1. Prune duplicate/conflicting events
        events_no_conflicts = self._resolve_conflicts(sorted_events)
        
        # 2. Enforce logical lifecycle (Install -> Use -> Uninstall)
        final_timeline = self._enforce_lifecycle(events_no_conflicts)
        
        if self._log_corrections and self._repairs:
            log.info("Timeline Validation: %d repairs performed", len(self._repairs))
            for repair in self._repairs:
                log.debug("  Repair: %s", repair)
                
        return final_timeline

    def _resolve_conflicts(self, events: list[TimelineEvent]) -> list[TimelineEvent]:
        """
        Detects events that overlap in a way that violates logical consistency.
        Currently handles strict temporal deduplication based on source authority.
        """
        seen: dict[tuple, TimelineEvent] = {}
        unique: list[TimelineEvent] = []

        for event in events:
            if not event.valid_time or event.timestamp is None:
                unique.append(event)
                continue
                
            # Conflict key: (timestamp, package, event_type)
            key = (event.timestamp, event.app, event.event_type)
            
            if key not in seen:
                seen[key] = event
                unique.append(event)
            else:
                existing = seen[key]
                new_rank = resolve_source_rank(event.source)
                old_rank = resolve_source_rank(existing.source)
                
                if new_rank < old_rank:
                    # Current event is more authoritative; replace the old one
                    unique.remove(existing)
                    unique.append(event)
                    seen[key] = event
                    event.reason = f"Conflict resolved: {event.source} (rank {new_rank}) preferred over {existing.source} (rank {old_rank})"
                    self._repairs.append(
                        f"REPLACED: {existing.source} with {event.source} for {key}"
                    )
                else:
                    existing.reason = f"Conflict resolved: {existing.source} (rank {old_rank}) preferred over {event.source} (rank {new_rank})"
                    self._repairs.append(
                        f"REJECTED: {event.source} (weaker than {existing.source}) for {key}"
                    )
        
        return sorted(unique, key=lambda e: (e.timestamp is None, e.timestamp))

    def _enforce_lifecycle(self, events: list[TimelineEvent]) -> list[TimelineEvent]:
        """
        Prunes usage after uninstall and usage before install.
        """
        # Track the state per application
        # State: { "pkg": { "installed": bool, "uninstalled_at": datetime | None } }
        from datetime import datetime
        app_states: dict[str, dict] = {}
        valid_events: list[TimelineEvent] = []

        for event in events:
            pkg = event.app
            if pkg not in app_states:
                app_states[pkg] = {"installed": False, "uninstalled_at": None}
            state = app_states[pkg]

            if not event.valid_time or event.timestamp is None:
                if state["installed"]:
                    event.add_flag("TEMPORAL_INTEGRITY_INVALID")
                valid_events.append(event)
                continue
                
                
            # ── Handle Lifecycle Events ──
            if event.event_type == "APP_INSTALLED":
                state["installed"] = True
                state["uninstalled_at"] = None  # Reset in case of reinstall
                valid_events.append(event)
                continue

            if event.event_type == "APP_UNINSTALLED":
                state["uninstalled_at"] = event.timestamp
                valid_events.append(event)
                continue

            # ── Handle Usage Events ──
            # Only apply strict lifecycle checks to "ACTIVE" events
            if event.event_type in ("APP_OPENED", "APP_CLOSED", "ACTIVITY_RESUMED", "APP_SESSION"):
                # Rule: No usage after uninstall
                if state["uninstalled_at"] and event.timestamp > state["uninstalled_at"]:
                    event.severity = "SUSPICIOUS"
                    event.reason = f"Temporal Paradox: Usage after uninstall recorded at {state['uninstalled_at']}"
                    self._repairs.append(
                        f"FLAGGED: Usage of {pkg} after APP_UNINSTALLED (severity upgraded)"
                    )
                    # We keep it as evidence of paradox, but mark it SUSPICIOUS
                
                # Rule: Install must precede usage
                future_install = self._has_future_install(events, pkg, event.timestamp)
                if future_install:
                    event.severity = "SUSPICIOUS"
                    event.reason = f"Temporal Paradox: Usage before install recorded at {future_install}"
                    self._repairs.append(
                        f"FLAGGED: Usage of {pkg} before APP_INSTALLED (severity upgraded)"
                    )

            valid_events.append(event)

        return valid_events

    def _has_future_install(self, events: list[TimelineEvent], pkg: str, timestamp) -> Optional[datetime]:
        """Check if an APP_INSTALLED event exists for this package after the given timestamp."""
        for e in events:
            if not e.valid_time or e.timestamp is None:
                continue
            if e.app == pkg and e.event_type == "APP_INSTALLED" and e.timestamp > timestamp:
                return e.timestamp
        return None
