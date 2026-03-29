"""
models/normalized_event.py
===========================
Output dataclass of the normalization pipeline.

A NormalizedEvent is a ParsedEvent that has passed through all normalization
stages:
  - Timestamp validated, bounded, and expressed in ISO 8601 UTC
  - Clock skew flagged (and optionally adjusted)
  - Noise classification applied
  - Near-duplicate window checked

Downstream consumers (timeline builder, correlation engine) work exclusively
with NormalizedEvent — they never touch raw ParsedEvent objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models.parsed_event import EvidenceType


@dataclass
class NormalizedEvent:
    """
    A fully validated, timestamp-normalised forensic event.

    Attributes:
        timestamp:            UTC-aware datetime (guaranteed non-None, in-bounds).
        iso_timestamp:        ISO 8601 string: ``2024-01-15T22:30:45.000000Z``.
                              Always UTC (Z suffix).  Ready for display and export.
        app:                  Cleaned package name (stripped, lower-cased).
        event_type:           Normalised upper-case event category string.
        source:               Originating artifact source identifier.
        evidence_type:        "DIRECT" at this stage; upgraded by later engines.
        raw_fields:           Original key-value pairs preserved for audit.
        source_command:       ADB command that produced the raw data.
        timestamp_approximate: True if the timestamp was inferred from
                              ``collected_at`` rather than parsed from the source.
        dedup_key:            Inherited SHA-1 key from ParsedEvent.
        normalization_flags:  Ordered list of flags applied during normalization.
                              Examples:
                                "TIMESTAMP_APPROXIMATE"      — timestamp was inferred
                                "CLOCK_SKEW_DETECTED"        — ts ahead of collection
                                "TIMESTAMP_FLOOR_APPLIED"    — ts predated Android era
                                "RATE_LIMITED"               — one of N collapsed events
                                "HIGH_FREQUENCY_COLLAPSED"   — burst of same event type
    """

    # ── Core fields (mirrored from ParsedEvent) ───────────────────────────────
    timestamp: Optional[datetime]
    iso_timestamp: str
    app: str
    event_type: str
    source: str
    evidence_type: "EvidenceType"
    raw_fields: dict
    source_command: str
    timestamp_approximate: bool
    dedup_key: str
    valid_time: bool = True

    # ── Normalization metadata ────────────────────────────────────────────────
    normalization_flags: list[str] = field(default_factory=list)

    def has_flag(self, flag: str) -> bool:
        """Return True if this event carries the given normalization flag."""
        return flag in self.normalization_flags

    def to_dict(self) -> dict:
        """
        Serialize to the canonical event format.

        Returns the standard event structure required by downstream consumers:
        {
            "timestamp":     datetime (UTC-aware),
            "iso_timestamp": str (ISO 8601 / Z),
            "app":           str,
            "event_type":    str,
            "source":        str,
            "evidence_type": str
        }
        """
        return {
            "timestamp":     self.timestamp,
            "iso_timestamp": self.iso_timestamp,
            "valid_time":    self.valid_time,
            "app":           self.app,
            "event_type":    self.event_type,
            "source":        self.source,
            "evidence_type": self.evidence_type,
        }

    def __repr__(self) -> str:
        flags = f" [{','.join(self.normalization_flags)}]" if self.normalization_flags else ""
        return (
            f"NormalizedEvent({self.iso_timestamp} | "
            f"{self.event_type:<25} | {self.app}{flags})"
        )
