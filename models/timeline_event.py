"""
models/timeline_event.py
=========================
The final, fully-enriched forensic event in the timeline.

A TimelineEvent is a NormalizedEvent that has been:
  1. Placed into the chronological timeline (TimelineBuilder)
  2. Optionally upgraded to CORRELATED by the CorrelationEngine
  3. Optionally upgraded to INFERRED, or had flags attached, by the InferenceEngine

This is the canonical data structure consumed by:
  - The GUI timeline view (QAbstractTableModel)
  - The report generator
  - The analysis panel (flags / suspicious events)

Forensic note:
  TimelineEvent is APPEND-ONLY in terms of classification upgrades.
  DIRECT → CORRELATED → INFERRED is a one-way promotion.
  No engine ever demotes or removes an event.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional, List
import uuid


EvidenceType = Literal["DIRECT", "CORRELATED", "INFERRED"]
SeverityLevel = Literal["NORMAL", "IMPORTANT", "SUSPICIOUS"]


# ─────────────────────────────────────────────────────────────────────────────
# TimelineEvent
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TimelineEvent:
    """
    A fully processed forensic event placed in the unified activity timeline.

    Core identity fields:
        event_id:        Unique identifier for this event (UUID4 string).
                         Used to cross-reference events in correlation groups.
        sequence_index:  Zero-based position in the final sorted timeline.
                         Assigned by TimelineBuilder; stable within a session.

    Temporal fields:
        timestamp:       UTC-aware datetime — the authoritative event time.
        iso_timestamp:   ISO 8601 string: ``2024-01-15T22:30:45.000000Z``.

    Event content:
        app:             Package name (lower-cased, sanitised).
        event_type:      Normalised event category (e.g. "APP_OPENED").
        source:          Artifact source identifier (e.g. "usage_stats").
        evidence_type:   DIRECT | CORRELATED | INFERRED.
        description:     Human-readable summary for display in the GUI/report.
        severity:        NORMAL | IMPORTANT | SUSPICIOUS.
        reason:          Detailed explanation of why this event or classification exists.

    Correlation fields:
        correlation_id:  Shared UUID string linking all events in the same
                         correlation group.  None if not correlated.
        correlated_with: List of ``event_id`` values this event is linked to.
        inferred_from:   List of ``event_id`` values that triggered this
                         INFERRED event.  Empty for DIRECT/CORRELATED events.
        linked_events:   Unified list of all related event IDs (for UI display).

    Behavioral flags:
        flags:           List of behavioral/suspicious flag strings.
                         Examples:
                           "LATE_NIGHT_ACTIVITY"        — activity 00:00–05:00
                           "COMMUNICATION_BURST"        — >5 msgs in 10 min
                           "RAPID_INSTALL_UNINSTALL"    — installed and removed same session
                           "IMMEDIATE_APP_USE"          — used within 60s of install
                           "DATA_EXFILTRATION_WINDOW"   — file + WiFi event cluster
                           "ACTIVITY_BLACKOUT"          — >6h gap during active hours
                           "APP_CAMOUFLAGE_SUSPECTED"   — package/label mismatch
                           "CLOCK_SKEW_DETECTED"        — device time ahead of collection
                           "FACTORY_RESET_INDICATOR"    — evidence of data wipe

    Provenance:
        raw_fields:      Original key-value pairs from the parsed source line.
        source_command:  The ADB command that produced the raw data.
        normalization_flags: Flags applied during normalization (e.g. "TIMESTAMP_APPROXIMATE").
        timestamp_approximate: True if timestamp was inferred from collected_at.
        dedup_key:       SHA-1 key inherited from ParsedEvent.
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sequence_index: int = 0

    # ── Temporal ──────────────────────────────────────────────────────────────
    timestamp: Optional[datetime] = None
    iso_timestamp: str = "UNKNOWN"
    valid_time: bool = True

    # ── Content ───────────────────────────────────────────────────────────────
    app: str = ""
    event_type: str = ""
    source: str = ""
    evidence_type: EvidenceType = "DIRECT"
    description: str = ""
    severity: SeverityLevel = "NORMAL"
    reason: str = "Raw extraction"

    # ── Correlation ───────────────────────────────────────────────────────────
    correlation_id: Optional[str] = None
    correlated_with: List[str] = field(default_factory=list)
    inferred_from: List[str] = field(default_factory=list)
    linked_events: List[str] = field(default_factory=list)

    # ── Behavioral flags ──────────────────────────────────────────────────────
    flags: list[str] = field(default_factory=list)

    # ── Provenance ────────────────────────────────────────────────────────────
    raw_fields: dict = field(default_factory=dict)
    source_command: str = ""
    normalization_flags: list[str] = field(default_factory=list)
    timestamp_approximate: bool = False
    dedup_key: str = ""

    # ── Helpers ───────────────────────────────────────────────────────────────

    @property
    def iso_timestamp_ist(self) -> str:
        """Returns the timestamp formatted in Indian Standard Time (UTC+05:30)."""
        if self.timestamp is None:
            return "UNKNOWN"
        # IST is UTC+05:30
        ist_time = self.timestamp.astimezone(timezone(timedelta(hours=5, minutes=30)))
        return ist_time.strftime("%Y-%m-%d %H:%M:%S")

    def add_flag(self, flag: str) -> None:
        """Append a behavioral flag if not already present."""
        if flag not in self.flags:
            self.flags.append(flag)

    def promote_to(self, evidence_type: EvidenceType) -> None:
        """
        Upgrade the evidence classification.
        Enforces the one-way promotion rule: DIRECT → CORRELATED → INFERRED.
        Silently ignores demotions.
        """
        _rank = {"DIRECT": 0, "CORRELATED": 1, "INFERRED": 2}
        if _rank.get(evidence_type, -1) > _rank.get(self.evidence_type, 0):
            self.evidence_type = evidence_type

    def link_correlation(self, correlation_id: str, peer_event_id: str) -> None:
        """Attach a correlation group ID and record a peer event link."""
        self.correlation_id = correlation_id
        if peer_event_id not in self.correlated_with:
            self.correlated_with.append(peer_event_id)
        if peer_event_id not in self.linked_events:
            self.linked_events.append(peer_event_id)

    def add_linked_event(self, event_id: str) -> None:
        """Add an event ID to the linked_events list."""
        if event_id not in self.linked_events:
            self.linked_events.append(event_id)

    def is_suspicious(self) -> bool:
        """Return True if this event carries any behavioral flags or is marked SUSPICIOUS."""
        return bool(self.flags) or self.severity == "SUSPICIOUS"

    def to_dict(self) -> dict:
        """Serialize to the canonical display/export format."""
        return {
            "event_id":       self.event_id,
            "sequence_index": self.sequence_index,
            "timestamp":      self.timestamp,
            "iso_timestamp":  self.iso_timestamp,
            "valid_time":     self.valid_time,
            "app":            self.app,
            "event_type":     self.event_type,
            "source":         self.source,
            "evidence_type":  self.evidence_type,
            "severity":       self.severity,
            "reason":         self.reason,
            "description":    self.description,
            "flags":          list(self.flags),
            "correlation_id": self.correlation_id,
            "linked_events":  list(self.linked_events),
        }

    def __repr__(self) -> str:
        flag_str = f" ⚑{self.flags}" if self.flags else ""
        sev_icon = {"NORMAL": "○", "IMPORTANT": "!", "SUSPICIOUS": "X"}.get(self.severity, "?")
        corr_str = f" [{self.evidence_type}]" if self.evidence_type != "DIRECT" else ""
        return (
            f"TimelineEvent(#{self.sequence_index} {sev_icon} {self.iso_timestamp} | "
            f"{self.event_type:<25} | {self.app}{corr_str}{flag_str})"
        )
