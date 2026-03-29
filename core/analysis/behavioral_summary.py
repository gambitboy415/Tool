"""
core/analysis/behavioral_summary.py
====================================
Aggregates behavioral statistics from a reconstructed forensic timeline.

Responsibility:
    Summarises sessions, active windows, and suspicious patterns for the
    final forensics report and analysis dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Dict, List

from models.timeline_event import TimelineEvent
from utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class BehavioralSummary:
    """
    Structured summary of user behavior reconstructed from the timeline.
    """
    total_active_duration: timedelta = field(default_factory=timedelta)
    session_count: int = 0
    top_apps_by_duration: List[tuple[str, float]] = field(default_factory=list)
    suspicious_event_count: int = 0
    important_event_count: int = 0
    activity_gaps_count: int = 0
    inferred_active_window: tuple[int, int] = (8, 22)
    
    def to_dict(self) -> dict:
        return {
            "total_active_time_sec": self.total_active_duration.total_seconds(),
            "session_count": self.session_count,
            "top_apps": self.top_apps_by_duration,
            "suspicious_count": self.suspicious_event_count,
            "important_count": self.important_event_count,
            "gap_count": self.activity_gaps_count,
            "active_window": f"{self.inferred_active_window[0]:02d}:00 - {self.inferred_active_window[1]:02d}:00"
        }


class BehavioralAnalyzer:
    """
    Analyzes a timeline to produce a BehavioralSummary.
    """

    def __init__(self, timeline: list[TimelineEvent]) -> None:
        self._timeline = timeline

    def generate_summary(self) -> BehavioralSummary:
        summary = BehavioralSummary()
        
        app_durations: Dict[str, float] = {}
        
        activity_hours = []

        for event in self._timeline:
            # 1. Tally severities
            if event.severity == "SUSPICIOUS":
                summary.suspicious_event_count += 1
            elif event.severity == "IMPORTANT":
                summary.important_event_count += 1
            
            # 2. Extract session stats
            if event.event_type == "APP_SESSION":
                duration = event.raw_fields.get("duration_sec", 0)
                summary.total_active_duration += timedelta(seconds=duration)
                summary.session_count += 1
                
                app_durations[event.app] = app_durations.get(event.app, 0) + duration
            
            # 3. Track activity for window inference
            if event.event_type in ("APP_OPENED", "USER_INTERACTION", "APP_SESSION") and event.timestamp:
                activity_hours.append(event.timestamp.hour)
            
            # 4. Count gaps
            if event.event_type == "ACTIVITY_GAP":
                summary.activity_gaps_count += 1

        # Calculate top apps
        summary.top_apps_by_duration = sorted(
            app_durations.items(), key=lambda x: x[1], reverse=True
        )[:5]
        
        # Infer window (re-using logic from gap rule for consistency)
        if len(activity_hours) > 10:
            sorted_hours = sorted(activity_hours)
            summary.inferred_active_window = (
                sorted_hours[int(len(sorted_hours) * 0.1)],
                sorted_hours[int(len(sorted_hours) * 0.9)]
            )

        return summary


def get_behavioral_summary(timeline: list[TimelineEvent]) -> BehavioralSummary:
    """Public helper to get behavioral summary."""
    return BehavioralAnalyzer(timeline).generate_summary()
