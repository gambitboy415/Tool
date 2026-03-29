"""
core/analytics/behavior_engine.py
==================================
Independent Behavior Analytics Engine for DroidTrace Pro.

Responsibility:
    - Provides high-level diagnostics on top of the final forensic timeline.
    - Computes app profiles, usage heatmaps, and risk classifications.
    - Detects behavioral anomalies (long sessions, night activity).
    - Filters and searches the timeline for analyst review.

Integration:
    This module works on the final list of TimelineEvent objects.
    It does not modify the original events.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Any, Optional

from models.timeline_event import TimelineEvent
from utils.logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 🛡️ Risk Classification Constants (Phase 4)
# ─────────────────────────────────────────────────────────────────────────────

SAFE_PREFIXES = [
    "com.android", "com.google", "com.samsung",
    "com.sec", "com.qualcomm", "com.miui",
    "com.oppo", "com.vivo", "com.huawei"
]

KNOWN_SAFE_APPS = [
    "com.whatsapp",
    "com.instagram.android",
    "com.facebook.katana",
    "com.google.android.youtube"
]


class BehaviorEngine:
    """
    Advanced Analytics Engine for post-timeline processing.
    """

    def __init__(self, timeline: List[TimelineEvent]) -> None:
        # Keep a private copy of the timeline (reference is fine as we don't mutate)
        self._timeline = timeline

    # ── Phase 2: App Profiling ───────────────────────────────────────────────

    def app_profiles(self) -> Dict[str, Dict[str, Any]]:
        """
        Compute usage statistics per application.
        """
        profiles: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "sessions": 0,
            "total_time": 0.0,
            "last_used": None,
            "installed": None
        })

        for event in self._timeline:
            app = event.app
            if not app or app == "system":
                continue

            # Track session count and duration
            if event.event_type == "APP_SESSION":
                profiles[app]["sessions"] += 1
                profiles[app]["total_time"] += event.raw_fields.get("duration_sec", 0.0)

            # Track last used timestamp
            if event.event_type in ("APP_OPENED", "APP_SESSION") and event.timestamp:
                current_last = profiles[app]["last_used"]
                if current_last is None or event.timestamp > current_last:
                    profiles[app]["last_used"] = event.timestamp

            # Track installation timestamp
            if event.event_type == "APP_INSTALLED" and event.timestamp:
                profiles[app]["installed"] = event.timestamp

        # Convert defaultdict to regular dict for return
        return dict(profiles)

    # ── Phase 3: Usage Heatmap ───────────────────────────────────────────────

    def usage_heatmap(self) -> Dict[int, int]:
        """
        Count event frequency per hour of the day (0–23).
        Targets user-initiated events (OPENED / SESSION).
        """
        heatmap = {h: 0 for h in range(24)}
        for event in self._timeline:
            if not event.timestamp or not event.valid_time:
                continue
            
            if event.event_type in ("APP_OPENED", "APP_SESSION", "USER_INTERACTION"):
                heatmap[event.timestamp.hour] += 1
        
        return heatmap

    # ── Phase 4: Risk Classification ─────────────────────────────────────────

    def classify_risk(self, app_name: str) -> str:
        """
        Categorize an app's risk profile based on its package name.
        """
        if any(app_name.startswith(pre) for pre in SAFE_PREFIXES):
            return "LOW (SYSTEM)"
        
        if app_name in KNOWN_SAFE_APPS:
            return "LOW (KNOWN)"
        
        return "MEDIUM"

    # ── Phase 5: App Lifecycle ───────────────────────────────────────────────

    def app_lifecycle(self, app_name: str) -> List[Dict[str, Any]]:
        """
        Extract the chronological sequence of lifecycle events for a specific app.
        """
        sequence = []
        target_events = ("APP_INSTALLED", "APP_SESSION", "APP_UPDATED")
        
        # Timeline is already sorted by builder, so we just filter
        for event in self._timeline:
            if event.app == app_name and event.event_type in target_events:
                sequence.append({
                    "timestamp": event.timestamp,
                    "event_type": event.event_type,
                    "description": event.description
                })
        
        return sequence

    # ── Phase 6: Device Summary ──────────────────────────────────────────────

    def device_summary(self) -> Dict[str, Any]:
        """
        Compute global usage metrics for the device.
        """
        profiles = self.app_profiles()
        
        total_sessions = sum(p["sessions"] for p in profiles.values())
        total_active_time = sum(p["total_time"] for p in profiles.values())
        
        most_used_app = "N/A"
        max_time = -1.0
        for app, stats in profiles.items():
            if stats["total_time"] > max_time:
                max_time = stats["total_time"]
                most_used_app = app
        
        return {
            "total_sessions": total_sessions,
            "total_active_time": total_active_time,
            "most_used_app": most_used_app
        }

    # ── Phase 7: System App Filter ───────────────────────────────────────────

    def filter_user_apps(self) -> List[TimelineEvent]:
        """
        Return a subset of the timeline excluding system-prefixed packages.
        """
        return [
            e for e in self._timeline 
            if not any(e.app.startswith(pre) for pre in SAFE_PREFIXES)
        ]

    # ── Phase 8: Anomaly Detection ───────────────────────────────────────────

    def detect_anomalies(self) -> List[str]:
        """
        Identify high-level behavioral anomalies.
        """
        anomalies = []
        
        # 1. Sessions longer than 2 hours (7200 seconds)
        for event in self._timeline:
            if event.event_type == "APP_SESSION":
                duration = event.raw_fields.get("duration_sec", 0.0)
                if duration > 7200:
                    anomalies.append(
                        f"Long Session: '{event.app}' used for {duration/3600:.1f} hours at {event.iso_timestamp}"
                    )
        
        # 2. Usage between 00:00 and 05:00
        night_use_apps = set()
        for event in self._timeline:
            if not event.timestamp or not event.valid_time:
                continue
            
            if event.event_type in ("APP_OPENED", "APP_SESSION"):
                if 0 <= event.timestamp.hour < 5:
                    night_use_apps.add(event.app)
        
        for app in night_use_apps:
            if not any(app.startswith(pre) for pre in SAFE_PREFIXES):
                anomalies.append(f"Late Night Activity: Non-system app '{app}' used between 00:00–05:00.")

        return anomalies

    # ── Phase 9: Search / Filter ─────────────────────────────────────────────

    def search(self, query: str) -> List[TimelineEvent]:
        """
        Case-insensitive search for events by app package name.
        """
        q = query.lower()
        return [e for e in self._timeline if q in e.app.lower()]

    # ── Public Summary API ────────────────────────────────────────────────────

    def generate_full_report(self) -> Dict[str, Any]:
        """
        Compile all analytics into a single coherent report.
        """
        summary = self.device_summary()
        profiles = self.app_profiles()
        
        # Annotate profiles with risk classification
        for app in profiles:
            profiles[app]["risk"] = self.classify_risk(app)

        return {
            "device": summary,
            "heatmap": self.usage_heatmap(),
            "anomalies": self.detect_anomalies(),
            "app_profiles": profiles
        }
