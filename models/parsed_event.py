"""
models/parsed_event.py
======================
Normalised, structured event produced by the parsing layer.

A ParsedEvent sits between raw ADB output (RawArtifact) and the final
TimelineEvent — it has been parsed and normalised but has not yet been
placed into the timeline, correlated, or had inference applied.

Forensic guarantee: every ParsedEvent carries a reference back to its
source command so the chain of custody is never broken.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional
import hashlib


# Evidence classification — only DIRECT is assigned at parse time.
# CORRELATED and INFERRED are set by later engine stages.
EvidenceType = Literal["DIRECT", "CORRELATED", "INFERRED"]


@dataclass
class ParsedEvent:
    """
    A single structured forensic event extracted from a RawArtifact.

    Attributes:
        timestamp:      UTC datetime of the event. May be approximated from
                        the artifact's ``collected_at`` if the raw data lacked
                        an explicit timestamp (see ``timestamp_approximate``).
        app:            Package name of the associated application, or a
                        descriptive label for system events (e.g. "android.system").
        event_type:     Normalised event category string.  Examples:
                            APP_FOREGROUND, APP_BACKGROUND, APP_INSTALLED,
                            APP_UPDATED, APP_UNINSTALLED, DEVICE_STARTUP,
                            DEVICE_SHUTDOWN, USER_INTERACTION.
        source:         Artifact source identifier (e.g. "usage_stats",
                        "package_detail", "app_list").
        evidence_type:  Always "DIRECT" at parse time.  Upgraded by later stages.
        raw_fields:     Original key-value pairs from the parsed line / block —
                        preserved for downstream parsers and report rendering.
        source_command: The exact ADB command that produced the raw data.
        timestamp_approximate: True if the timestamp was inferred (e.g. from
                        ``collected_at``) rather than read from the artifact.
        dedup_key:      SHA-1 hash used to detect duplicate events across
                        sources.  Computed deterministically from
                        (timestamp epoch, app, event_type).
    """

    timestamp: Optional[datetime]
    app: str
    event_type: str
    source: str
    evidence_type: EvidenceType = "DIRECT"
    raw_fields: dict = field(default_factory=dict)
    source_command: str = ""
    timestamp_approximate: bool = False
    valid_time: bool = True
    reason: str = "Raw extraction"

    # Computed post-init
    dedup_key: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.dedup_key = self._compute_dedup_key()

    def _compute_dedup_key(self) -> str:
        """
        SHA-1 of (timestamp_epoch_ms, app, event_type).
        Epoch milliseconds are used so minor sub-second differences in how
        parsers read the same timestamp don't create false duplicates.
        """
        if self.timestamp is not None:
            epoch_ms = int(self.timestamp.timestamp() * 1000)
            prefix = str(epoch_ms)
        else:
            prefix = "UNKNOWN"
            
        raw = f"{prefix}|{self.app.strip().lower()}|{self.event_type.strip().upper()}"
        return hashlib.sha1(raw.encode()).hexdigest()

    def to_dict(self) -> dict:
        """Serialize to a plain dict matching the required event format."""
        return {
            "timestamp":   self.timestamp,
            "app":         self.app,
            "event_type":  self.event_type,
            "source":      self.source,
            "evidence_type": self.evidence_type,
        }

    def __repr__(self) -> str:
        ts = self.timestamp.strftime("%Y-%m-%d %H:%M:%S") if self.timestamp else "UNKNOWN"
        approx = "~" if self.timestamp_approximate and self.timestamp else ""
        return (
            f"ParsedEvent({approx}{ts} | {self.event_type:<22} | "
            f"{self.app} | {self.source})"
        )
