"""
models/report_data.py
======================
Data model for a complete forensic report snapshot.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from models.device_info import DeviceInfo
    from models.timeline_event import TimelineEvent
    from core.analysis.behavioral_summary import BehavioralSummary

@dataclass
class ReportData:
    """
    Aggregated snapshot of all forensic findings for a single analysis session.
    Passed to a renderer to produce the final output file.
    """
    device: DeviceInfo
    collection_time: datetime
    report_time: datetime
    tool_version: str
    timeline: list[TimelineEvent]

    # Derived views (computed by ReportGenerator)
    flagged_events: list[TimelineEvent] = field(default_factory=list)
    inferred_events: list[TimelineEvent] = field(default_factory=list)
    correlated_events: list[TimelineEvent] = field(default_factory=list)
    suspicious_apps: list[str] = field(default_factory=list)
    flag_summary: dict[str, int] = field(default_factory=dict)   # flag → count
    source_summary: dict[str, int] = field(default_factory=dict) # source → event count
    behavioral_summary: Optional[BehavioralSummary] = None       # Aggregated behavioral stats
    stats: dict = field(default_factory=dict)                    # engine stats
