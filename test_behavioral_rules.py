import os
from datetime import datetime, timezone, timedelta
from core.timeline.timeline_builder import build_timeline
from core.inference.inference_engine import InferenceEngine
from models.timeline_event import TimelineEvent
from utils.logger import get_logger
from config import settings

log = get_logger(__name__)

def test_behavioral_rules():
    now = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    
    # ── Test Events ──
    timeline = [
        # 1. Late Night Activity (settings.NIGHT_HOURS_START is 0)
        TimelineEvent(
            timestamp=datetime(2024, 1, 15, 2, 0, 0, tzinfo=timezone.utc),
            app="com.whatsapp",
            event_type="APP_OPENED",
            source="usage_stats",
            description="User opened WhatsApp at 2 AM"
        ),
        
        # 2. Immediate App Use
        TimelineEvent(
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
            app="com.malicious.tool",
            event_type="APP_INSTALLED",
            source="package_detail"
        ),
        TimelineEvent(
            timestamp=datetime(2024, 1, 15, 10, 0, 30, tzinfo=timezone.utc), # 30s later
            app="com.malicious.tool",
            event_type="APP_OPENED",
            source="usage_stats"
        ),

        # 3. Communication Burst (5 events in 10 mins)
        TimelineEvent(timestamp=datetime(2024, 1, 15, 11, 0, 0, tzinfo=timezone.utc), app="com.whatsapp", event_type="SMS_SENT"),
        TimelineEvent(timestamp=datetime(2024, 1, 15, 11, 1, 0, tzinfo=timezone.utc), app="com.whatsapp", event_type="SMS_SENT"),
        TimelineEvent(timestamp=datetime(2024, 1, 15, 11, 2, 0, tzinfo=timezone.utc), app="com.whatsapp", event_type="SMS_SENT"),
        TimelineEvent(timestamp=datetime(2024, 1, 15, 11, 3, 0, tzinfo=timezone.utc), app="com.whatsapp", event_type="SMS_SENT"),
        TimelineEvent(timestamp=datetime(2024, 1, 15, 11, 4, 0, tzinfo=timezone.utc), app="com.whatsapp", event_type="SMS_SENT"),

        # 4. Silent Service (No preceding APP_OPENED)
        TimelineEvent(
            timestamp=datetime(2024, 1, 15, 15, 0, 0, tzinfo=timezone.utc),
            app="com.hidden.tracker",
            event_type="FOREGROUND_SERVICE_START",
            source="usage_stats"
        ),

        # 5. Rapid Install/Uninstall
        TimelineEvent(
            timestamp=datetime(2024, 1, 15, 16, 0, 0, tzinfo=timezone.utc),
            app="com.temp.app",
            event_type="APP_INSTALLED",
            source="package_detail"
        ),
        TimelineEvent(
            timestamp=datetime(2024, 1, 15, 16, 15, 0, tzinfo=timezone.utc), # 15 mins later
            app="com.temp.app",
            event_type="APP_UNINSTALLED",
            source="package_detail"
        ),
        
        # 6. Anti-Forensic Sequence
        TimelineEvent(
            timestamp=datetime(2024, 1, 15, 17, 0, 0, tzinfo=timezone.utc),
            app="com.evidence.wiper",
            event_type="SYSTEM_INTERACTION",
            description="Cleared application cache"
        ),
        TimelineEvent(
            timestamp=datetime(2024, 1, 15, 17, 1, 0, tzinfo=timezone.utc),
            app="com.evidence.wiper",
            event_type="APP_UNINSTALLED"
        )
    ]

    # Pre-indexing
    for idx, e in enumerate(timeline):
        e.sequence_index = idx
        e.iso_timestamp = e.timestamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if e.timestamp else "UNKNOWN"

    # Run Inference
    engine = InferenceEngine()
    report = engine.run(timeline)
    
    print("\nInference Report:")
    print(report.summary())
    
    print("\nFlagged Events:")
    for e in timeline:
        if e.flags:
            print(f"[{e.iso_timestamp}] {e.app:<20} | {e.event_type:<20} | Flags: {e.flags}")

    # Assertions
    # 1. Late Night
    assert any("LATE_NIGHT_ACTIVITY" in e.flags for e in timeline if e.app == "com.whatsapp")
    # 2. Immediate Use
    assert any("IMMEDIATE_APP_USE" in e.flags for e in timeline if e.app == "com.malicious.tool")
    # 3. Comm Burst
    assert any(e.event_type == "COMMUNICATION_BURST" for e in timeline)
    # 4. Silent Service
    assert any("SILENT_BACKGROUND_SERVICE" in e.flags for e in timeline if e.app == "com.hidden.tracker")
    # 5. Rapid Cycle
    assert any("RAPID_INSTALL_UNINSTALL" in e.flags for e in timeline if e.app == "com.temp.app")
    # 6. Anti-Forensic
    assert any(e.event_type == "ANTI_FORENSIC_SEQUENCE" for e in timeline if e.app == "com.evidence.wiper")

    print("\nVerification SUCCESS: All behavioral forensic rules triggered correctly.")

if __name__ == "__main__":
    test_behavioral_rules()
