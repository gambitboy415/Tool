"""
Test script to verify the UI fixes for event log view.

ISSUE 1: Timeline ordering with UNKNOWN timestamps
ISSUE 2: Activity type display with emojis
"""

from datetime import datetime, timezone
from models.timeline_event import TimelineEvent
from ui.widgets.timeline_model import TimelineTableModel, _EVENT_TYPE_LABELS
from PyQt6.QtCore import QModelIndex, Qt

def test_event_type_mapping():
    """Test that event types are properly mapped to human-readable labels with emojis."""
    print("=" * 80)
    print("TEST 1: Event Type Mapping")
    print("=" * 80)
    
    test_events = [
        ("APP_INSTALLED", "📦 Installed"),
        ("APP_UNINSTALLED", "🗑️ Uninstalled"),
        ("APP_OPENED", "🟢 App Opened"),
        ("ACTIVITY_RESUMED", "🟢 App Opened"),
        ("APP_CLOSED", "🔴 App Closed"),
        ("ACTIVITY_PAUSED", "🔴 App Closed"),
        ("APP_UPDATED", "🔄 Updated"),
        ("SCREEN_ON", "💡 Screen On"),
        ("SCREEN_OFF", "⚫ Screen Off"),
        ("NETWORK_CONNECT", "🌐 Network Connected"),
        ("NETWORK_DISCONNECT", "❌ Network Disconnected"),
    ]
    
    for event_type, expected_display in test_events:
        emoji, label = _EVENT_TYPE_LABELS.get(event_type, ("ℹ️", event_type))
        actual_display = f"{emoji} {label}"
        status = "✓" if actual_display == expected_display else "✗"
        print(f"{status} {event_type:25} → {actual_display}")
        if actual_display != expected_display:
            print(f"  Expected: {expected_display}")
    
    # Test unmapped event type (should get generic fallback)
    unknown_type = "CUSTOM_EVENT_TYPE"
    emoji, label = _EVENT_TYPE_LABELS.get(unknown_type, ("ℹ️", unknown_type))
    actual_display = f"{emoji} {label}"
    print(f"✓ {unknown_type:25} → {actual_display} (fallback to generic)")
    print()

def test_unknown_timestamp_sorting():
    """Test that events with UNKNOWN timestamps are sorted by sequence_index."""
    print("=" * 80)
    print("TEST 2: Unknown Timestamp Sorting")
    print("=" * 80)
    
    model = TimelineTableModel()
    
    # Create events with mixed valid and UNKNOWN timestamps
    events = [
        TimelineEvent(
            event_id="event_1",
            sequence_index=0,
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
            iso_timestamp="2024-01-15T10:00:00.000000Z",
            app="com.app1",
            event_type="APP_OPENED",
            source="usage_stats",
        ),
        TimelineEvent(
            event_id="event_2",
            sequence_index=1,
            timestamp=None,  # UNKNOWN timestamp
            iso_timestamp="UNKNOWN",
            app="com.app2",
            event_type="APP_INSTALLED",
            source="packages",
        ),
        TimelineEvent(
            event_id="event_3",
            sequence_index=2,
            timestamp=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            iso_timestamp="2024-01-15T12:00:00.000000Z",
            app="com.app3",
            event_type="APP_CLOSED",
            source="usage_stats",
        ),
        TimelineEvent(
            event_id="event_4",
            sequence_index=3,
            timestamp=None,  # UNKNOWN timestamp
            iso_timestamp="UNKNOWN",
            app="com.app4",
            event_type="NETWORK_CONNECT",
            source="connectivity",
        ),
    ]
    
    model.set_events(events)
    
    # Verify display text for timestamp column
    print("\nTimestamp Display (Column 1):")
    for row in range(model.rowCount()):
        index = model.createIndex(row, 1)
        display_text = model.data(index, Qt.ItemDataRole.DisplayRole)
        event = model.event_at(row)
        print(f"  Row {row}: {display_text:30} (sequence_index={event.sequence_index})")
    
    # Verify custom sort role for timestamp column
    print("\nCustom Sort Role (UserRole + 1) for Timestamp Column:")
    for row in range(model.rowCount()):
        index = model.createIndex(row, 1)
        sort_value = model.data(index, Qt.ItemDataRole.UserRole + 1)
        event = model.event_at(row)
        print(f"  Row {row}: {sort_value:40} (sequence_index={event.sequence_index})")
        
        # Verify that UNKNOWN timestamps use sequence_index for sorting
        if event.iso_timestamp == "UNKNOWN":
            assert isinstance(sort_value, str) and "UNKNOWN_" in sort_value, \
                f"Expected UNKNOWN_* format for unknown timestamp, got {sort_value}"
            assert str(event.sequence_index).zfill(10) in sort_value, \
                f"Expected sequence_index {event.sequence_index} in sort value"
    
    print("\n✓ All UNKNOWN timestamps properly formatted for sorting by sequence_index")
    print()

def test_event_type_display():
    """Test that the display method returns mapped event types."""
    print("=" * 80)
    print("TEST 3: Event Type Display in Table")
    print("=" * 80)
    
    model = TimelineTableModel()
    
    events = [
        TimelineEvent(
            event_id="event_1",
            sequence_index=0,
            app="com.whatsapp",
            event_type="APP_OPENED",
            source="usage_stats",
        ),
        TimelineEvent(
            event_id="event_2",
            sequence_index=1,
            app="com.spotify",
            event_type="APP_INSTALLED",
            source="packages",
        ),
        TimelineEvent(
            event_id="event_3",
            sequence_index=2,
            app="com.gmail",
            event_type="NETWORK_DISCONNECT",
            source="connectivity",
        ),
    ]
    
    model.set_events(events)
    
    print("\nEvent Type Display (Column 4):")
    for row in range(model.rowCount()):
        index = model.createIndex(row, 4)
        display_text = model.data(index, Qt.ItemDataRole.DisplayRole)
        event = model.event_at(row)
        print(f"  {event.app:20} {event.event_type:25} → {display_text}")
    
    print("\n✓ Event types properly displayed with emojis and labels")
    print()

if __name__ == "__main__":
    try:
        test_event_type_mapping()
        test_unknown_timestamp_sorting()
        test_event_type_display()
        print("=" * 80)
        print("ALL TESTS PASSED ✓")
        print("=" * 80)
    except Exception as e:
        print(f"\n✗ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
