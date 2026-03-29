import os
from datetime import datetime, timezone, timedelta
from core.inference.inference_engine import InferenceEngine
from core.reporting.html_renderer import HtmlRenderer
from models.timeline_event import TimelineEvent

def main():
    now = datetime.now(timezone.utc)
    
    # Create timeline events with a gap to test ActivityGapRule and some late night for LateNightActivityRule
    events = [
        # Activity at 09:00
        TimelineEvent(
            timestamp=now.replace(hour=9, minute=0, second=0, microsecond=0),
            iso_timestamp=now.replace(hour=9, minute=0).isoformat(),
            app="com.whatsapp",
            event_type="APP_OPENED",
            source="test",
            evidence_type="USAGE",
            sequence_index=0
        ),
        # Activity at 10:00
        TimelineEvent(
            timestamp=now.replace(hour=10, minute=0, second=0, microsecond=0),
            iso_timestamp=now.replace(hour=10, minute=0).isoformat(),
            app="com.whatsapp",
            event_type="APP_OPENED",
            source="test",
            evidence_type="USAGE",
            sequence_index=1
        ),
        # Gap of 4 hours -> Activity at 14:00 (Should trigger ACTIVITY_GAP)
        TimelineEvent(
            timestamp=now.replace(hour=14, minute=0, second=0, microsecond=0),
            iso_timestamp=now.replace(hour=14, minute=0).isoformat(),
            app="com.whatsapp",
            event_type="USER_INTERACTION",
            source="test",
            evidence_type="USAGE",
            sequence_index=2
        ),
        # Late night activity at 03:00 (Should trigger ACTIVITY_OUTSIDE_WINDOW)
        TimelineEvent(
            timestamp=now.replace(hour=3, minute=0, second=0, microsecond=0),
            iso_timestamp=now.replace(hour=3, minute=0).isoformat(),
            app="com.facebook.orca",
            event_type="APP_OPENED",
            source="test",
            evidence_type="USAGE",
            sequence_index=3
        ),
        # Exfiltration window test
        TimelineEvent(
            timestamp=now.replace(hour=14, minute=5, second=0, microsecond=0),
            iso_timestamp=now.replace(hour=14, minute=5).isoformat(),
            app="system",
            event_type="FILE_MODIFIED",
            source="test",
            evidence_type="FS",
            sequence_index=4
        ),
        TimelineEvent(
            timestamp=now.replace(hour=14, minute=6, second=0, microsecond=0),
            iso_timestamp=now.replace(hour=14, minute=6).isoformat(),
            app="system",
            event_type="WIFI_CONNECTED",
            source="test",
            evidence_type="NET",
            sequence_index=5
        )
    ]
    
    engine = InferenceEngine()
    report = engine.run(events)
    print("Inference completed:")
    print(report.summary())
    
    print("\nInferred Events:")
    for e in events:
        if e.evidence_type == "INFERRED":
            print(f"- {e.event_type} | Severity: {e.severity} | Reason: {e.reason}")
        if e.flags:
            print(f"- [FLAGGED] {e.event_type} | Flags: {e.flags} | Severity: {e.severity} | Reason: {e.reason}")
            
    renderer = HtmlRenderer()
    output_path = os.path.join(os.getcwd(), "test_report.html")
    # we need device info
    from models.device_info import DeviceInfo
    from models.report_data import ReportData
    from pathlib import Path
    device = DeviceInfo(serial="DUMMY123", model="Test Device", manufacturer="Test", android_version="13", sdk_version=33, build_fingerprint="dummy/build", connected_at=now)
    report_data = ReportData(
        device=device,
        collection_time=now,
        report_time=now,
        tool_version="debug",
        timeline=events,
        inferred_events=[e for e in events if e.evidence_type == "INFERRED"],
        flagged_events=[e for e in events if e.flags]
    )
    renderer.render(report_data, Path(output_path))
    print(f"\nReport generated at {output_path}")

if __name__ == "__main__":
    main()
