"""
core/correlation/rules/app_lifecycle_rules.py
===============================================
Correlation rules for application lifecycle event pairs.

Rules defined here:
  1. AppLifecycleRule      — Links APP_INSTALLED to first APP_OPENED.
  2. SuspiciousRemovalRule — Links heavy usage sessions to subsequent uninstalls.
  3. DormantAppRule        — Identifies installed apps with zero user sessions.
  4. BackgroundActivityRule — Links activity sessions to SCREEN_OFF states.

Forensic relevance:
  - AppLifecycle: Establishes "intent to use" following installation.
  - SuspiciousRemoval: Detects potential data clearing or anti-forensic behavior.
  - DormantApp: Highlights potential pre-installed bloatware or unvisited artifacts.
  - BackgroundActivity: Detects non-user intent activity (potentially malicious).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from config.settings import (
    CORRELATION_WINDOW_SECONDS,
    IMMEDIATE_USE_THRESHOLD_SECONDS,
)
from core.correlation.rules.base_rule import CorrelationRule
from models.timeline_event import TimelineEvent
from utils.logger import get_logger

log = get_logger(__name__)


# ── Phase 4/5: Safe Prefix Filtering ─────────────────────────────────────────
_SAFE_PREFIXES: frozenset[str] = frozenset({
    "com.android", "com.google", "com.samsung",
    "com.sec", "com.qualcomm", "vendor.", "android."
})

def is_safe_app(app: str) -> bool:
    return any(app.startswith(p) for p in _SAFE_PREFIXES)


class AppLifecycleRule(CorrelationRule):
    """
    Phase 6 Rule 1: Install + Usage -> APP_LIFECYCLE.
    Links APP_INSTALLED to the first APP_OPENED for the same package.
    """
    NAME = "AppLifecycle"
    DESCRIPTION = "Links installation events to subsequent usage markers."

    def apply(self, timeline: list[TimelineEvent]) -> int:
        hits = 0
        installs = {e.app: e for e in timeline if e.event_type == "APP_INSTALLED"}
        
        for event in timeline:
            if event.event_type == "APP_OPENED":
                install = installs.get(event.app)
                if install and install.timestamp and event.timestamp and event.timestamp > install.timestamp:
                    corr_id = str(uuid.uuid4())
                    install.promote_to("CORRELATED")
                    install.link_correlation(corr_id, event.event_id)
                    install.reason = f"App Lifecycle: Installation confirmed by subsequent usage."
                    
                    event.promote_to("CORRELATED")
                    event.link_correlation(corr_id, install.event_id)
                    
                    hits += 1
                    del installs[event.app]  # Only correlate first usage
        return hits


class SuspiciousRemovalRule(CorrelationRule):
    """
    Phase 6 Rule 2: Long session + uninstall -> SUSPICIOUS_REMOVAL.
    """
    NAME = "SuspiciousRemoval"
    DESCRIPTION = "Flags uninstalls that occur shortly after significant usage."

    _WINDOW = timedelta(minutes=10)
    _MIN_SESSION_SEC = 120

    def apply(self, timeline: list[TimelineEvent]) -> int:
        hits = 0
        for i, event in enumerate(timeline):
            if event.event_type != "APP_UNINSTALLED":
                continue

            for j in range(i - 1, -1, -1):
                prev = timeline[j]
                if event.timestamp is None or prev.timestamp is None: continue
                if event.timestamp - prev.timestamp > self._WINDOW: break
                
                if prev.app == event.app and prev.event_type == "APP_SESSION":
                    duration = prev.raw_fields.get("duration_sec", 0)
                    if duration >= self._MIN_SESSION_SEC:
                        event.severity = "SUSPICIOUS"
                        event.add_flag("SUSPICIOUS") # Phase 8: Only flag SUSPICIOUS
                        event.reason = f"Suspicious Removal: App removed within 10m of heavy usage ({int(duration)}s)."
                        
                        corr_id = str(uuid.uuid4())
                        event.link_correlation(corr_id, prev.event_id)
                        prev.link_correlation(corr_id, event.event_id)
                        hits += 1
                        break
        return hits


class DormantAppRule(CorrelationRule):
    """
    Phase 6 Rule 3: Install + no usage -> DORMANT_APP.
    Phase 5: Only mark dormant if no usage sessions AND not system/vendor.
    """
    NAME = "DormantApp"
    DESCRIPTION = "Identifies installed apps with no detected usage."

    def apply(self, timeline: list[TimelineEvent]) -> int:
        hits = 0
        installed_at: dict[str, TimelineEvent] = {}
        used_apps: set[str] = set()

        for event in timeline:
            if event.event_type == "APP_INSTALLED":
                installed_at[event.app] = event
            if event.event_type == "APP_SESSION":
                used_apps.add(event.app)

        for pkg, event in installed_at.items():
            if pkg not in used_apps and not is_safe_app(pkg):
                event.severity = "IMPORTANT"
                # Phase 8: user didn't specify DORMANT as a flag, but it's suspicious pattern
                event.add_flag("SUSPICIOUS") 
                event.reason = "Dormant App: Installed but no user session detected (non-system app)."
                hits += 1
        return hits


class BackgroundActivityRule(CorrelationRule):
    """
    Phase 6 Rule 4: App used while screen OFF -> BACKGROUND_ACTIVITY.
    """
    NAME = "BackgroundActivity"
    DESCRIPTION = "Flags app usage sessions that occur while the screen is OFF."

    def apply(self, timeline: list[TimelineEvent]) -> int:
        hits = 0
        screen_is_off = False
        
        for event in timeline:
            if event.event_type == "SCREEN_OFF":
                screen_is_off = True
            elif event.event_type == "SCREEN_ON":
                screen_is_off = False
            
            if screen_is_off and event.event_type == "APP_SESSION" and not is_safe_app(event.app):
                event.severity = "SUSPICIOUS"
                event.add_flag("SUSPICIOUS")
                event.reason = "Background Activity: App session recorded while screen was OFF."
                hits += 1
        return hits
