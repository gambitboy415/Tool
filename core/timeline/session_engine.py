"""
core/timeline/session_engine.py
===============================
Session Reconstruction Engine for DroidTrace Pro.

Responsibility:
    Takes a chronological list of TimelineEvents and groups raw app lifecycle
    markers (FOREGROUND/BACKGROUND) into logical 'Sessions'.

Logic:
    - A session starts at APP_FOREGROUND or ACTIVITY_RESUMED.
    - A session ends at APP_BACKGROUND, or when a different app takes the foreground.
    - Captures duration, sequence of sub-events, and calculates session density.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from models.timeline_event import TimelineEvent
from utils.logger import get_logger

log = get_logger(__name__)


class SessionBuilder:
    """
    Groups raw app markers into structured session events.

    The builder scans the timeline and synthesizes APP_SESSION events.
    Logic:
      - Session starts at APP_OPENED.
      - Session ends at APP_CLOSED or the next APP_OPENED (Implicit Close).
      - Hybrid Cap: 
          - SOFT_CAP (30m): Default for inferred sessions.
          - HARD_CAP (2h): Maximum allowed for any single session.
      - Session Types:
          - EXACT: Marker indicates a real close.
          - INFERRED_SOFT: Terminated by next event or soft cap.
          - INFERRED_HARD: Terminated by hard cap.
    """

    SOFT_CAP_SEC = 30 * 60    # 30 minutes
    HARD_CAP_SEC = 120 * 60   # 2 hours

    def __init__(self, events: list[TimelineEvent], collection_time: Optional[datetime] = None) -> None:
        # Sort by timestamp, handling None values
        self._events = sorted(events, key=lambda e: (e.timestamp is None, e.timestamp))
        self._collection_time = collection_time

    def build(self, summarize: bool = False) -> list[TimelineEvent]:
        """
        Reconstruct sessions and return the updated timeline.
        """
        if not self._events:
            return []

        log.info("Starting behavioral session reconstruction for %d events...", len(self._events))
        
        sessions: list[TimelineEvent] = []
        raw_to_remove: set[str] = set()
        
        # Track active session
        active_session: Optional[dict] = None

        for event in self._events:
            # Ignore invalid times for session boundaries
            if not event.valid_time or event.timestamp is None:
                continue
                
            # --- START Event Detection ---
            if event.event_type == "APP_OPENED":
                # If there was an active session for a DIFFERENT app, close it first
                if active_session and active_session["app"] != event.app:
                    sessions.append(self._close_active_session(active_session, event.timestamp, is_implicit=True))
                    active_session = None

                # Start new session if none active
                if not active_session:
                    active_session = {
                        "app": event.app,
                        "start_time": event.timestamp,
                        "events": [event.event_id],
                        "source": event.source,
                        "start_reason": event.reason
                    }
                else:
                    # Same app opened again (multi-activity)
                    active_session["events"].append(event.event_id)
                
                if summarize:
                    raw_to_remove.add(event.event_id)

            # --- END Event Detection ---
            elif event.event_type == "APP_CLOSED":
                if active_session and active_session["app"] == event.app:
                    active_session["events"].append(event.event_id)
                    sessions.append(self._close_active_session(active_session, event.timestamp, is_implicit=False))
                    active_session = None
                    
                    if summarize:
                        raw_to_remove.add(event.event_id)

        # Handle dangling session at end of data
        if active_session:
            end_time = self._collection_time or datetime.now(tz=timezone.utc)
            sessions.append(self._close_active_session(active_session, end_time, is_implicit=True))

        # Build final timeline
        result = [e for e in self._events if e.event_id not in raw_to_remove]
        result.extend(sessions)
        
        # Re-sort and re-index
        result.sort(key=lambda e: (e.timestamp is None, e.timestamp))
        for idx, event in enumerate(result):
            event.sequence_index = idx
            
        log.info("Behavioral engine: synthesized %d sessions", len(sessions))
        return result

    def _close_active_session(self, session: dict, end_time: datetime, is_implicit: bool) -> TimelineEvent:
        """Apply hybrid caps and determine session type."""
        start_time = session["start_time"]
        raw_diff_sec = (end_time - start_time).total_seconds()
        
        # Determine Session Type and apply caps
        session_type = "EXACT" if not is_implicit else "INFERRED_SOFT"
        duration_sec = raw_diff_sec

        # 1. Check Hard Cap
        if raw_diff_sec > self.HARD_CAP_SEC:
            duration_sec = self.HARD_CAP_SEC
            session_type = "INFERRED_HARD"
        
        # 2. Check Soft Cap for Inferred sessions
        elif is_implicit and raw_diff_sec > self.SOFT_CAP_SEC:
            duration_sec = self.SOFT_CAP_SEC
            session_type = "INFERRED_SOFT"

        final_end_time = start_time + timedelta(seconds=duration_sec)
        
        # Determine severity
        severity = "NORMAL"
        if duration_sec > 3600: severity = "SUSPICIOUS"
        elif duration_sec > 1200: severity = "IMPORTANT"

        reason = f"Reconstructed {session_type} session. Boundary: {'Implicit' if is_implicit else 'Explicit marker'}"
        description = f"User session duration: {self._format_duration(duration_sec)} ({session_type})"

        return TimelineEvent(
            timestamp=start_time,
            iso_timestamp=start_time.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            app=session["app"],
            event_type="APP_SESSION",
            source=session["source"],
            evidence_type="CORRELATED" if session_type == "EXACT" else "INFERRED",
            severity=severity,
            reason=reason,
            description=description,
            linked_events=session["events"],
            raw_fields={
                "duration_sec": duration_sec,
                "session_type": session_type,
                "end_time": final_end_time.isoformat(),
                "original_reason": session["start_reason"]
            }
        )

    @staticmethod
    def _format_duration(seconds: float) -> str:
        if seconds < 60: return f"{int(seconds)}s"
        return f"{int(seconds // 60)}m {int(seconds % 60)}s" if seconds < 3600 else \
               f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"


def build_sessions(timeline: list[TimelineEvent], collection_time: Optional[datetime] = None, summarize: bool = False) -> list[TimelineEvent]:
    return SessionBuilder(timeline, collection_time).build(summarize=summarize)
