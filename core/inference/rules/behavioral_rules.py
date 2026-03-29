"""
core/inference/rules/behavioral_rules.py
==========================================
Deterministic behavioral pattern inference rules for DroidTrace Pro.
Phase 8 compliant: Consolidated flagging into ACTIVITY_GAP, SUSPICIOUS, INVALID.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional

from core.inference.rules.base_inference import InferenceRule
from models.timeline_event import TimelineEvent
from utils.logger import get_logger
from config import settings

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: create a synthetic INFERRED event
# ─────────────────────────────────────────────────────────────────────────────

def _make_inferred(
    timestamp: Optional[datetime],
    event_type: str,
    description: str,
    source_events: list[TimelineEvent],
    app: str = "system",
    flag: str = "",
) -> TimelineEvent:
    """
    Build a new INFERRED TimelineEvent synthesised from a behavioral pattern.
    """
    iso_ts = timestamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if timestamp else "UNKNOWN"
    te = TimelineEvent(
        timestamp=timestamp,
        iso_timestamp=iso_ts,
        valid_time=timestamp is not None,
        app=app,
        event_type=event_type,
        source="inference_engine",
        evidence_type="INFERRED",
        description=description,
        inferred_from=[e.event_id for e in source_events],
        sequence_index=-1,
    )
    if flag:
        te.add_flag(flag)
    log.debug("Synthesised INFERRED event: %s (%s)", event_type, description[:60])
    return te


# ─────────────────────────────────────────────────────────────────────────────
# Rule 1: Adaptive Activity Gap Detection (Phase 8 compliant)
# ─────────────────────────────────────────────────────────────────────────────

class ActivityGapRule(InferenceRule):
    """
    Detects timeline gaps > 2 hours during the user's active window.
    """

    NAME = "ActivityGap"
    DESCRIPTION = "Detects >2h gaps during inferred active user hours."

    _GAP_THRESHOLD = timedelta(hours=2)

    def apply(self, timeline: list[TimelineEvent]) -> list[TimelineEvent]:
        valid_timeline = [e for e in timeline if getattr(e, 'valid_time', True) and e.timestamp is not None]
        if len(valid_timeline) < 2:
            return []

        # Infer active hours from data
        activity_hours = [e.timestamp.hour for e in valid_timeline if e.event_type in ("APP_OPENED", "USER_INTERACTION", "APP_SESSION")]
        if not activity_hours:
            activity_start, activity_end = 8, 22
        else:
            sorted_hours = sorted(activity_hours)
            active_start = sorted_hours[int(len(sorted_hours) * 0.1)]
            active_end = sorted_hours[int(len(sorted_hours) * 0.9)]

        inferred_events: list[TimelineEvent] = []
        sorted_tl = sorted(valid_timeline, key=lambda e: e.timestamp)

        i = 0
        while i < len(sorted_tl) - 1:
            a = sorted_tl[i]
            b = sorted_tl[i+1]
            gap = b.timestamp - a.timestamp
            
            if gap >= self._GAP_THRESHOLD and (active_start <= a.timestamp.hour <= active_end):
                # Check for consecutive gaps and merge
                j = i + 1
                while j < len(sorted_tl) - 1:
                    next_a = sorted_tl[j]
                    next_b = sorted_tl[j+1]
                    next_gap = next_b.timestamp - next_a.timestamp
                    if next_gap >= self._GAP_THRESHOLD:
                        j += 1
                        continue
                    break
                
                final_b = sorted_tl[j]
                total_gap = final_b.timestamp - a.timestamp
                
                inferred = _make_inferred(
                    timestamp=a.timestamp,
                    event_type="ACTIVITY_GAP",
                    description=f"Inferred Blackout: {total_gap.total_seconds()/3600:.1f} hour activity gap detected.",
                    source_events=[a, final_b],
                    flag="ACTIVITY_GAP",
                )
                inferred_events.append(inferred)
                i = j
            else:
                i += 1
        return inferred_events


# ─────────────────────────────────────────────────────────────────────────────
# Rule 2: App Camouflage Detection (Phase 8 compliant)
# ─────────────────────────────────────────────────────────────────────────────

class AppCamouflageRule(InferenceRule):
    """
    Detects package name impersonation and suspicious install locations.
    """

    NAME = "AppCamouflage"
    DESCRIPTION = "Flags packages with system-pretending names or suspicious paths."

    _SYSTEM_PREFIXES = ("com.android.", "com.google.", "android.", "com.samsung.")

    def apply(self, timeline: list[TimelineEvent]) -> list[TimelineEvent]:
        for event in timeline:
            if event.event_type != "APP_INSTALLED":
                continue
            pkg = event.app
            apk_loc = event.raw_fields.get("apk_location", "")
            is_user = apk_loc == "user" or "/data/app" in event.raw_fields.get("apk_path", "").lower()

            if is_user and any(pkg.startswith(p) for p in self._SYSTEM_PREFIXES):
                event.add_flag("SUSPICIOUS") # Phase 8 Requirement
                event.severity = "SUSPICIOUS"
                event.reason = f"App Camouflage: User-space app '{pkg}' uses system prefix."
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Rule 3: Timestamp Integrity Rule (Phase 8 compliant)
# ─────────────────────────────────────────────────────────────────────────────

class TimestampIntegrityRule(InferenceRule):
    """
    Flags apps where >30% events carry invalid timestamps.
    """

    NAME = "TimestampIntegrity"
    DESCRIPTION = "Flags apps with high temporal corruption."

    _RATIO_THRESHOLD = 0.30
    _MIN_EVENTS = 3

    def apply(self, timeline: list[TimelineEvent]) -> list[TimelineEvent]:
        by_app: dict[str, list[TimelineEvent]] = defaultdict(list)
        for e in timeline: 
            if e.app and e.app != "system": by_app[e.app].append(e)

        for app, events in by_app.items():
            if len(events) < self._MIN_EVENTS: continue
            invalid = [e for e in events if not getattr(e, 'valid_time', True) or e.timestamp is None]
            ratio = len(invalid) / len(events)

            if ratio >= self._RATIO_THRESHOLD:
                for e in events:
                    if not getattr(e, 'valid_time', True) or e.timestamp is None:
                        e.add_flag("TEMPORAL_INTEGRITY_INVALID") # Phase 8 Requirement (Revised)
                        e.severity = "IMPORTANT"
                        e.reason = f"Temporal Corruption: App '{app}' has {ratio*100:.1f}% missing timestamps."
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Rule 4: Late Night Activity
# ─────────────────────────────────────────────────────────────────────────────

class LateNightActivityRule(InferenceRule):
    """
    Flags user interactions occurring during 'night' hours.
    """
    NAME = "LateNightActivity"
    DESCRIPTION = f"Flags activity between {settings.NIGHT_HOURS_START:02d}:00 and {settings.NIGHT_HOURS_END:02d}:00."

    def apply(self, timeline: list[TimelineEvent]) -> list[TimelineEvent]:
        for event in timeline:
            if not event.timestamp or not event.valid_time:
                continue
            
            # Target user-initiated events only
            if event.event_type in ("APP_OPENED", "USER_INTERACTION", "APP_SESSION"):
                hour = event.timestamp.hour
                start = settings.NIGHT_HOURS_START
                end = settings.NIGHT_HOURS_END
                
                is_night = False
                if start <= end:
                    is_night = start <= hour < end
                else: # Crosses midnight (e.g. 23:00 - 05:00)
                    is_night = hour >= start or hour < end

                if is_night:
                    event.add_flag("LATE_NIGHT_ACTIVITY")
                    event.severity = "IMPORTANT"
                    event.reason = f"Late Night Activity: User interaction at {hour:02d}:00 ({event.app})."
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Rule 5: Immediate App Use
# ─────────────────────────────────────────────────────────────────────────────

class ImmediateAppUseRule(InferenceRule):
    """
    Flags apps used almost immediately after installation.
    """
    NAME = "ImmediateAppUse"
    DESCRIPTION = f"Flags apps used within {settings.IMMEDIATE_USE_THRESHOLD_SECONDS}s of installation."

    def apply(self, timeline: list[TimelineEvent]) -> list[TimelineEvent]:
        valid_tl = [e for e in timeline if e.timestamp and e.valid_time]
        installs = {e.app: e for e in valid_tl if e.event_type == "APP_INSTALLED"}
        
        for event in valid_tl:
            if event.event_type in ("APP_OPENED", "APP_SESSION"):
                install = installs.get(event.app)
                if install:
                    diff = (event.timestamp - install.timestamp).total_seconds()
                    if 0 <= diff <= settings.IMMEDIATE_USE_THRESHOLD_SECONDS:
                        event.add_flag("IMMEDIATE_APP_USE")
                        event.severity = "SUSPICIOUS"
                        event.reason = f"Immediate App Use: '{event.app}' used {diff:.1f}s after install."
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Rule 6: Communication Burst
# ─────────────────────────────────────────────────────────────────────────────

class CommunicationBurstRule(InferenceRule):
    """
    Flags high-frequency communication activity.
    """
    NAME = "CommunicationBurst"
    DESCRIPTION = f"Flags >{settings.BURST_EVENT_THRESHOLD} comms events in {settings.BURST_WINDOW_SECONDS/60:.0f}m."

    def apply(self, timeline: list[TimelineEvent]) -> list[TimelineEvent]:
        # Filter for communication markers (SMS, Call, Messaging apps)
        comm_types = ("SMS_SENT", "SMS_RECEIVED", "CALL_OUTGOING", "CALL_INCOMING", "MESSAGE_SENT")
        comm_events = [e for e in timeline if (e.event_type in comm_types or e.app in ("com.whatsapp", "com.signal", "com.telegram")) and e.timestamp and e.valid_time]
        
        if len(comm_events) < settings.BURST_EVENT_THRESHOLD:
            return []

        sorted_comm = sorted(comm_events, key=lambda e: e.timestamp)
        inferred_events = []

        for i in range(len(sorted_comm) - settings.BURST_EVENT_THRESHOLD + 1):
            window = sorted_comm[i : i + settings.BURST_EVENT_THRESHOLD]
            start_ts = window[0].timestamp
            end_ts = window[-1].timestamp
            
            if (end_ts - start_ts).total_seconds() <= settings.BURST_WINDOW_SECONDS:
                # Merge overlapping bursts
                inferred = _make_inferred(
                    timestamp=start_ts,
                    event_type="COMMUNICATION_BURST",
                    description=f"Communication Burst: {len(window)} events detected within {settings.BURST_WINDOW_SECONDS/60:.0f} minutes.",
                    source_events=window,
                    flag="SUSPICIOUS"
                )
                inferred_events.append(inferred)
                # Skip forward past this window to avoid flood of inferred events for same burst
                # i += settings.BURST_EVENT_THRESHOLD - 1
        
        return inferred_events


# ─────────────────────────────────────────────────────────────────────────────
# Rule 7: Silent Background Service Activity
# ─────────────────────────────────────────────────────────────────────────────

class SilentServiceRule(InferenceRule):
    """
    Detects app services starting without preceding user interaction.
    """
    NAME = "SilentService"
    DESCRIPTION = "Flags background services starting without recent UI interaction."

    _UI_LOOKBACK_WINDOW = timedelta(minutes=5)

    def apply(self, timeline: list[TimelineEvent]) -> list[TimelineEvent]:
        valid_tl = [e for e in timeline if e.timestamp and e.valid_time]
        ui_events = [e for e in valid_tl if e.event_type in ("APP_OPENED", "USER_INTERACTION")]
        
        for event in valid_tl:
            if event.event_type == "FOREGROUND_SERVICE_START":
                # Check if this app had any UI activity recently
                recent_ui = [
                    u for u in ui_events 
                    if u.app == event.app and 0 <= (event.timestamp - u.timestamp).total_seconds() <= self._UI_LOOKBACK_WINDOW.total_seconds()
                ]
                
                if not recent_ui and event.app != "android" and not event.app.startswith("com.google."):
                    event.add_flag("SILENT_BACKGROUND_SERVICE")
                    event.severity = "IMPORTANT"
                    event.reason = f"Silent Service: '{event.app}' started a foreground service without preceding UI interaction."
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Rule 8: Rapid Install/Uninstall
# ─────────────────────────────────────────────────────────────────────────────

class RapidInstallUninstallRule(InferenceRule):
    """
    Flags packages installed and then removed within a very short window.
    """
    NAME = "RapidInstallUninstall"
    DESCRIPTION = "Flags packages uninstalled within 1 hour of installation."

    _MAX_WINDOW = timedelta(hours=1)

    def apply(self, timeline: list[TimelineEvent]) -> list[TimelineEvent]:
        valid_tl = [e for e in timeline if e.timestamp and e.valid_time]
        installs = {e.app: e for e in valid_tl if e.event_type == "APP_INSTALLED"}
        uninstalls = {e.app: e for e in valid_tl if e.event_type == "APP_UNINSTALLED"}
        
        for app, uninstall in uninstalls.items():
            install = installs.get(app)
            if install:
                diff = (uninstall.timestamp - install.timestamp).total_seconds()
                if 0 <= diff <= self._MAX_WINDOW.total_seconds():
                    uninstall.add_flag("RAPID_INSTALL_UNINSTALL")
                    uninstall.severity = "SUSPICIOUS"
                    uninstall.reason = f"Rapid Cycle: App '{app}' uninstalled {diff/60:.1f}m after installation."
                    install.add_flag("RAPID_INSTALL_UNINSTALL")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Rule 9: Anti-Forensic Sequence Detection
# ─────────────────────────────────────────────────────────────────────────────

class AntiForensicSequenceRule(InferenceRule):
    """
    Detects sequences like (Clear Cache/Data -> Uninstall).
    """
    NAME = "AntiForensicSequence"
    DESCRIPTION = "Detects data wiping followed by app removal."

    def apply(self, timeline: list[TimelineEvent]) -> list[TimelineEvent]:
        # Note: 'Clear Cache' and 'Clear Data' often appear in usage stats or logcat as specific flags
        # For now, we simulate this based on specific event sequences if detected.
        # This rule is compound and looks for specific sequences.
        inferred_events = []
        valid_tl = sorted([e for e in timeline if e.timestamp and e.valid_time], key=lambda e: e.timestamp)
        
        for i in range(len(valid_tl) - 1):
            a = valid_tl[i]
            b = valid_tl[i+1]
            
            # Sequence: (Something indicating wipe) -> UNINSTALL within 5 mins
            if a.app == b.app and b.event_type == "APP_UNINSTALLED":
                if "clear" in a.description.lower() or "wipe" in a.description.lower() or "cache" in a.description.lower():
                    if (b.timestamp - a.timestamp).total_seconds() <= 300:
                        inferred = _make_inferred(
                            timestamp=b.timestamp,
                            event_type="ANTI_FORENSIC_SEQUENCE",
                            description=f"Anti-Forensic Sequence: User cleared data and immediately uninstalled '{a.app}'.",
                            source_events=[a, b],
                            app=a.app,
                            flag="SUSPICIOUS"
                        )
                        inferred_events.append(inferred)
        return inferred_events


# ─────────────────────────────────────────────────────────────────────────────
# Rule 10: Factory Reset Indicator
# ─────────────────────────────────────────────────────────────────────────────

class FactoryResetIndicatorRule(InferenceRule):
    """
    Synthesises a marker if system logs suggest a factory reset.
    """
    NAME = "FactoryResetIndicator"
    DESCRIPTION = "Detects evidence of system-wide data wiping (Factory Reset)."

    def apply(self, timeline: list[TimelineEvent]) -> list[TimelineEvent]:
        # Typically detected by:
        # 1. Total absence of old logs before a specific 'DEVICE_STARTUP'
        # 2. Presence of 'com.google.android.setupwizard' activity
        
        inferred_events = []
        setup_wizard = [e for e in timeline if "setupwizard" in e.app.lower() and e.event_type == "APP_OPENED"]
        
        if setup_wizard:
            first_setup = min(setup_wizard, key=lambda e: (e.timestamp is None, e.timestamp))
            inferred = _make_inferred(
                timestamp=first_setup.timestamp,
                event_type="FACTORY_RESET_INDICATOR",
                description="Factory Reset Detected: System setup wizard activity found in forensic timeline.",
                source_events=setup_wizard,
                flag="SUSPICIOUS"
            )
            inferred_events.append(inferred)
        
        return inferred_events
