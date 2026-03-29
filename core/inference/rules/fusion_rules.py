"""
core/inference/rules/fusion_rules.py
======================================
Deterministic Behavioral Fusion rules for DroidTrace Pro.

These rules consolidate multi-source artifacts into high-level forensic 
conclusions (Sessions, Lifecycle, Network attribution).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from core.inference.rules.base_inference import InferenceRule
from core.inference.rules.behavioral_rules import _make_inferred
from models.timeline_event import TimelineEvent
from utils.logger import get_logger

log = get_logger(__name__)

from config import settings

def is_safe_app(app: str) -> bool:
    """True if app is a system package that can be ignored for baseline noise."""
    return any(app.startswith(p) for p in settings.SAFE_PREFIXES)


class AppLifecycleFusionRule(InferenceRule):
    """
    Phase 6 Rule 1: App Lifecycle Correlation.
    Synthesises a high-level lifecycle event when an app is confirmed used after install.
    """
    NAME = "AppLifecycleFusion"
    DESCRIPTION = "Synthesises lifecyle confirmation events for installed apps."

    def apply(self, timeline: list[TimelineEvent]) -> list[TimelineEvent]:
        valid_tl = [e for e in timeline if e.timestamp is not None]
        installs = {e.app: e for e in valid_tl if e.event_type == "APP_INSTALLED"}
        inferred_events = []

        for event in valid_tl:
            if event.event_type == "APP_SESSION":
                install = installs.get(event.app)
                if install and install.timestamp <= event.timestamp:
                    # Synthesise high-level conclusion
                    inferred = _make_inferred(
                        timestamp=event.timestamp,
                        event_type="APP_LIFECYCLE",
                        description=f"App Lifecycle: Package '{event.app}' installation confirmed by first user session.",
                        source_events=[install, event],
                        app=event.app,
                        flag="LIFECYCLE_CORRELATED"
                    )
                    inferred_events.append(inferred)
                    # Deduplicate: only one lifecycle marker per app
                    del installs[event.app]
        return inferred_events


class SuspiciousRemovalFusionRule(InferenceRule):
    """
    Phase 6 Rule 2: Suspicious Removal.
    Flags sessions > 30m followed by uninstall within 1 hour.
    """
    NAME = "SuspiciousRemovalFusion"
    DESCRIPTION = "Flags sessions > 30m followed by uninstall within 1 hour."

    _MAX_UNINSTALL_WINDOW = timedelta(hours=1)

    def apply(self, timeline: list[TimelineEvent]) -> list[TimelineEvent]:
        valid_tl = [e for e in timeline if e.timestamp is not None]
        uninstalls = {e.app: e for e in valid_tl if e.event_type == "APP_UNINSTALLED"}

        for event in valid_tl:
            if event.event_type == "APP_SESSION":
                duration = event.raw_fields.get("duration_sec", 0)
                if duration > 1800: # 30 minutes
                    uninstall = uninstalls.get(event.app)
                    if uninstall and timedelta(0) <= (uninstall.timestamp - event.timestamp) <= self._MAX_UNINSTALL_WINDOW:
                        event.severity = "SUSPICIOUS"
                        event.add_flag("SUSPICIOUS") 
                        event.reason = f"Suspicious Removal: Heavy usage ({duration/60:.1f}m) followed by uninstall."
                        uninstall.severity = "SUSPICIOUS"
                        uninstall.add_flag("SUSPICIOUS")
        return []


class DormantAppFusionRule(InferenceRule):
    """
    Phase 6 Rule 3: Dormant App Detection.
    Synthesises a DORMANT_APP marker for unused installations.
    """
    NAME = "DormantAppFusion"
    DESCRIPTION = "Synthesises markers for installed apps with zero user sessions."

    def apply(self, timeline: list[TimelineEvent]) -> list[TimelineEvent]:
        installs = {e.app: e for e in timeline if e.event_type == "APP_INSTALLED"}
        used_apps = {e.app for e in timeline if e.event_type in ("APP_SESSION", "APP_OPENED")}
        inferred_events = []
        
        for pkg, install in installs.items():
            if pkg not in used_apps and not is_safe_app(pkg) and pkg != "system":
                inferred = _make_inferred(
                    timestamp=install.timestamp,
                    event_type="DORMANT_APP",
                    description=f"Dormant Application: Package '{pkg}' is installed but zero usage sessions detected.",
                    source_events=[install],
                    app=pkg,
                    flag="SUSPICIOUS"
                )
                inferred_events.append(inferred)
        return inferred_events


class HeavyUsageFusionRule(InferenceRule):
    """
    Intelligence Layer: Heavy Usage Detection.
    Synthesises a HEAVY_USAGE marker for any session > 30 minutes.
    """
    NAME = "HeavyUsageFusion"
    DESCRIPTION = "Synthesises markers for application sessions exceeding 30 minutes."

    def apply(self, timeline: list[TimelineEvent]) -> list[TimelineEvent]:
        inferred_events = []
        for event in timeline:
            if event.event_type == "APP_SESSION":
                duration_sec = event.raw_fields.get("duration_sec", 0)
                if duration_sec > 1800: # 30 minutes
                    inferred = _make_inferred(
                        timestamp=event.timestamp,
                        event_type="HEAVY_USAGE",
                        description=f"Heavy Usage: Session for '{event.app}' lasted {duration_sec/60:.1f} minutes.",
                        source_events=[event],
                        app=event.app,
                        flag="SUSPICIOUS" if not is_safe_app(event.app) else ""
                    )
                    inferred_events.append(inferred)
        return inferred_events


class BackgroundActivityFusionRule(InferenceRule):
    """
    Phase 6 Rule 4: Background Activity (Screen Off).
    """
    NAME = "BackgroundActivityFusion"
    DESCRIPTION = "Detects app usage sessions starting during SCREEN_OFF periods."

    def apply(self, timeline: list[TimelineEvent]) -> list[TimelineEvent]:
        valid_tl = [e for e in timeline if e.timestamp is not None]
        sorted_tl = sorted(valid_tl, key=lambda e: e.timestamp)
        
        screen_is_on = True # Default assumption

        for event in sorted_tl:
            if event.event_type == "SCREEN_ON":
                screen_is_on = True
            elif event.event_type == "SCREEN_OFF":
                screen_is_on = False
            
            if event.event_type == "APP_SESSION" and not screen_is_on:
                if not is_safe_app(event.app) and event.app != "system":
                    event.add_flag("SUSPICIOUS") 
                    event.severity = "SUSPICIOUS"
                    event.reason = "Background Activity: Session recorded while device screen was OFF."
        return []
