
from datetime import datetime, timezone
from models.timeline_event import TimelineEvent
from models.normalized_event import NormalizedEvent
from core.timeline.timeline_builder import build_timeline
from ui.widgets.timeline_model import TimelineTableModel
from PyQt6.QtCore import Qt

def test_indexing_and_sorting():
    print("--- Testing 1-Based Indexing and Sorting ---")
    events = [
        NormalizedEvent(timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
                        iso_timestamp="2024-01-01T12:00:00.000000Z", app="app1", event_type="APP_OPENED", 
                        source="test", evidence_type="DIRECT", raw_fields={}, source_command="test",
                        timestamp_approximate=False, dedup_key="d1", valid_time=True),
        NormalizedEvent(timestamp=datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
                        iso_timestamp="2024-01-01T10:00:00.000000Z", app="app2", event_type="APP_CLOSED", 
                        source="test", evidence_type="DIRECT", raw_fields={}, source_command="test",
                        timestamp_approximate=False, dedup_key="d2", valid_time=True),
        NormalizedEvent(timestamp=None, iso_timestamp="UNKNOWN", app="app3", event_type="SCREEN_ON", 
                        source="test", evidence_type="DIRECT", raw_fields={}, source_command="test",
                        timestamp_approximate=False, dedup_key="d3", valid_time=False),
    ]
    
    # build_timeline should sort by timestamp and then assign sequence_index starting from 1
    timeline = build_timeline(events)
    
    # Expected order: app2 (10:00), app1 (12:00), app3 (UNKNOWN)
    assert timeline[0].app == "app2"
    assert timeline[0].sequence_index == 1
    assert timeline[1].app == "app1"
    assert timeline[1].sequence_index == 2
    assert timeline[2].app == "app3"
    assert timeline[2].sequence_index == 3
    print("✓ sequence_index is 1-based and follows chronological order (UNKNOWN at end).")

def test_model_numerical_sort():
    print("\n--- Testing Model Numerical Sort Role ---")
    model = TimelineTableModel()
    events = [
        TimelineEvent(event_id="e1", sequence_index=1, app="a", event_type="t", source="s", evidence_type="DIRECT"),
        TimelineEvent(event_id="e2", sequence_index=10, app="b", event_type="t", source="s", evidence_type="DIRECT"),
    ]
    model.set_events(events)
    
    # Column 0 Sort Role
    val1 = model.data(model.index(0, 0), Qt.ItemDataRole.UserRole + 1)
    val2 = model.data(model.index(1, 0), Qt.ItemDataRole.UserRole + 1)
    
    assert val1 == 1
    assert val2 == 10
    assert val1 < val2  # Numerical comparison, not string "1" > "10"
    print("✓ Column 0 returns integer for sort role (1 < 10).")

def test_event_labels():
    print("\n--- Testing New Event Labels ---")
    model = TimelineTableModel()
    test_cases = [
        ("USER_INTERACTION", "🖱️ User Interaction"),
        ("SHORTCUT_INVOCATION", "⚡ Shortcut Used"),
        ("KEYGUARD_SHOWN", "🔐 Device Locked"),
        ("DEVICE_STARTUP", "🔋 Device Startup"),
        ("SCREEN_ON", "💡 Screen On"),
    ]
    
    for etype, expected_label in test_cases:
        event = TimelineEvent(event_id="x", sequence_index=1, app="test", event_type=etype, source="test", evidence_type="DIRECT")
        model.set_events([event])
        display = model.data(model.index(0, 4), Qt.ItemDataRole.DisplayRole)
        print(f"  {etype:25} -> {display}")
        assert display == expected_label
    print("✓ All new event types map to correct emojis and labels.")

def test_fuzzy_parser():
    print("\n--- Testing Fuzzy Usage Stats Parser (Android 16 / Samsung) ---")
    from core.parsers.parser import UsageStatsParser
    from models.raw_artifact import RawArtifact, ArtifactType
    from datetime import datetime, timezone
    
    parser = UsageStatsParser()
    # Sample Samsung Android 16-style dump
    raw_content = """
    eventType=1 packageName=com.whatsapp time=1711710000123
    pkgName:com.samsung.android.calendar event:2 ts:1711710005456
    package="ai.perplexity.app.android" type="MOVE_TO_FOREGROUND" timeMillis="1711710010000"
    """
    artifact = RawArtifact(
        artifact_type=ArtifactType.USAGE_STATS,
        source_command="adb shell dumpsys usagestats",
        raw_output=raw_content,
        collected_at=datetime.now(tz=timezone.utc),
        device_serial="test_serial"
    )
    
    events = parser.parse(artifact)
    print(f"  Extracted {len(events)} events from fuzzy dump.")
    
    apps = [e.app for e in events]
    print(f"  Apps found: {apps}")
    # Print raw_fields for the first event if exists
    if events:
        print(f"  First event: {events[0].app} | {events[0].event_type} | {events[0].raw_fields}")
    
    assert "com.whatsapp" in apps
    assert "com.samsung.android.calendar" in apps
    assert "ai.perplexity.app.android" in apps
    
    types = [e.event_type for e in events]
    print(f"  Types found: {types}")
    assert "APP_OPENED" in types
    assert "APP_CLOSED" in types
    print("✓ Fuzzy parser correctly extracts events from diverse key-value formats.")

if __name__ == "__main__":
    try:
        test_indexing_and_sorting()
        test_model_numerical_sort()
        test_event_labels()
        test_fuzzy_parser()
        print("\nALL VERIFICATIONS PASSED!")
    except Exception as e:
        print(f"\nVERIFICATION FAILED: {e}")
        import traceback
        traceback.print_exc()
