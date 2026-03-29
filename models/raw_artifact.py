"""
models/raw_artifact.py
======================
Dataclass representing a single unprocessed ADB output blob.

A RawArtifact is the direct, unmodified output of one ADB command.
It is the earliest data structure in the processing pipeline — produced
by a Collector and consumed by a Parser.  Keeping it separate from
parsed data maintains a clear forensic chain-of-custody (we always
know exactly what the device returned before any transformation).
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto


class ArtifactType(str, Enum):
    """
    Enumeration of all supported artifact source types.
    Using str-mixin allows JSON serialization without extra steps.
    """
    CALL_LOG        = "call_log"
    SMS             = "sms"
    APP_LIST        = "app_list"
    APP_DETAIL      = "app_detail"
    BATTERY         = "battery"
    POWER           = "power"       # screen state
    NETWORK         = "network"
    LOCATION        = "location"
    NOTIFICATION    = "notification"
    USAGE_STATS     = "usage_stats"
    FILESYSTEM      = "filesystem"
    UNKNOWN         = "unknown"


@dataclass
class RawArtifact:
    """
    Raw, unprocessed output from a single ADB collection command.

    Attributes:
        artifact_type:  Category of the artifact (from ArtifactType enum).
        source_command: The exact ADB command that produced this output — for
                        audit trail and reproducibility.
        raw_output:     The raw stdout string returned by ADB.
        collected_at:   UTC datetime when the command completed.
        device_serial:  Serial of the device this was collected from.
        error:          Any stderr or exception message; None if collection succeeded.
        metadata:       Optional key-value pairs (e.g. {"package": "com.example"}).
    """

    artifact_type: ArtifactType
    source_command: str
    raw_output: str
    collected_at: datetime
    device_serial: str
    error: str | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def is_successful(self) -> bool:
        """True if the collection completed without error and produced output."""
        return self.error is None and bool(self.raw_output.strip())

    @property
    def line_count(self) -> int:
        """Number of lines in the raw output — useful for large-output diagnostics."""
        return self.raw_output.count("\n")

    def __repr__(self) -> str:
        status = "OK" if self.is_successful else f"ERR:{self.error[:40]}"
        return (
            f"RawArtifact(type={self.artifact_type.value}, "
            f"lines={self.line_count}, status={status})"
        )
