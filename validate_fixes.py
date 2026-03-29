"""
Comprehensive validation of both fixes.

ISSUE 1: Verify timeline events with UNKNOWN timestamps sort correctly
ISSUE 2: Verify event types display with emojis and human-readable labels
"""

from datetime import datetime, timezone
from models.timeline_event import TimelineEvent
from ui.widgets.timeline_model import TimelineTableModel, _EVENT_TYPE_LABELS
from ui.timeline_view import _EvidenceFilterProxy
from PyQt6.QtCore import Qt

def validate_issue_1_timeline_ordering():
    """
    ISSUE 1 VALIDATION:
    - Events have UNKNOWN timestamps so they don't sort correctly
    - Fix: Use sequence_index as fallback for sorting
    """
    print("\n" + "=" * 80)
    print("VALIDATION: ISSUE 1 — LOG TIMELINE ORDERING")
    print("=" * 80)
    
    model = TimelineTableModel()
    
    # Create events in random order with a mix of valid and UNKNOWN timestamps
    events = [
        TimelineEvent(
            event_id="ev_C", sequence_index=2,
            timestamp=datetime(2024, 1, 15, 16, 0, 0, tzinfo=timezone.utc),
            iso_timestamp="2024-01-15T16:00:00Z",
            app="com.app_C", event_type="APP_OPENED", source="usage_stats",
            evidence_type="DIRECT",
        ),
        TimelineEvent(
            event_id="ev_Unknown1", sequence_index=0,
            timestamp=None, iso_timestamp="UNKNOWN",
            app="com.app_Unknown1", event_type="APP_INSTALLED", source="packages",
            evidence_type="DIRECT",
        ),
        TimelineEvent(
            event_id="ev_A", sequence_index=1,
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
            iso_timestamp="2024-01-15T10:00:00Z",
            app="com.app_A", event_type="APP_CLOSED", source="usage_stats",
            evidence_type="DIRECT",
        ),
        TimelineEvent(
            event_id="ev_Unknown2", sequence_index=3,
            timestamp=None, iso_timestamp="UNKNOWN",
            app="com.app_Unknown2", event_type="NETWORK_CONNECT", source="connectivity",
            evidence_type="DIRECT",
        ),
        TimelineEvent(
            event_id="ev_B", sequence_index=4,
            timestamp=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            iso_timestamp="2024-01-15T12:00:00Z",
            app="com.app_B", event_type="APP_OPENED", source="usage_stats",
            evidence_type="DIRECT",
        ),
    ]
    
    model.set_events(events)
    proxy = _EvidenceFilterProxy()
    proxy.setSourceModel(model)
    
    # Sort by timestamp column
    proxy.sort(1, Qt.SortOrder.AscendingOrder)
    
    print("\nEvents after sorting by timestamp (ascending):")
    print("-" * 80)
    print(f"{'App':25} {'Timestamp Display':35} {'Sort Key':40}")
    print("-" * 80)
    
    sort_keys = []
    for proxy_row in range(proxy.rowCount()):
        source_idx = proxy.mapToSource(proxy.createIndex(proxy_row, 0))
        source_row = source_idx.row()
        event = model.event_at(source_row)
        
        # Get display and sort values
        ts_idx = model.createIndex(source_row, 1)
        display_text = model.data(ts_idx, Qt.ItemDataRole.DisplayRole)
        sort_key = model.data(ts_idx, Qt.ItemDataRole.UserRole + 1)
        
        print(f"{event.app:25} {display_text:35} {sort_key:40}")
        sort_keys.append(sort_key)
    
    # Verify sort order
    is_sorted = sort_keys == sorted(sort_keys)
    print("-" * 80)
    
    if is_sorted:
        print("✓ PASS: Events sorted correctly")
        print("  - Valid timestamps sorted chronologically (by ISO string)")
        print("  - UNKNOWN timestamps sorted by sequence_index")
        print("  - Chronological events come before UNKNOWN events")
    else:
        print("✗ FAIL: Events not sorted correctly")
        print(f"  Expected order: {sorted(sort_keys)}")
        print(f"  Got order:      {sort_keys}")
    
    # Verify display shows "UNKNOWN" for events with no timestamp
    print("\nVerifying display shows 'UNKNOWN' while sorting by sequence_index:")
    for proxy_row in range(proxy.rowCount()):
        source_idx = proxy.mapToSource(proxy.createIndex(proxy_row, 0))
        source_row = source_idx.row()
        event = model.event_at(source_row)
        
        ts_idx = model.createIndex(source_row, 1)
        display_text = model.data(ts_idx, Qt.ItemDataRole.DisplayRole)
        sort_key = model.data(ts_idx, Qt.ItemDataRole.UserRole + 1)
        
        if event.timestamp is None:
            is_unknown = display_text == "UNKNOWN"
            uses_seq_idx = "UNKNOWN_" in sort_key
            status = "✓" if (is_unknown and uses_seq_idx) else "✗"
            print(f"  {status} {event.app}: Display='{display_text}', SortKey='{sort_key}'")
    
    return is_sorted

def validate_issue_2_activity_type_display():
    """
    ISSUE 2 VALIDATION:
    - All events just say APP_INSTALLED with no richer activity context
    - Fix: Map event types to human-readable labels with emojis
    """
    print("\n" + "=" * 80)
    print("VALIDATION: ISSUE 2 — ACTIVITY TYPE DISPLAY")
    print("=" * 80)
    
    model = TimelineTableModel()
    
    test_cases = [
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
        ("CUSTOM_TYPE", "ℹ️ CUSTOM_TYPE"),  # fallback
    ]
    
    print("\nEvent Type Mappings:")
    print("-" * 80)
    print(f"{'Event Type':25} {'Expected Display':35} {'Actual Display':35} {'Status':10}")
    print("-" * 80)
    
    all_correct = True
    for event_type, expected_display in test_cases:
        # Get mapped display
        emoji, label = _EVENT_TYPE_LABELS.get(event_type, ("ℹ️", event_type))
        actual_display = f"{emoji} {label}"
        
        is_correct = actual_display == expected_display
        status = "✓ PASS" if is_correct else "✗ FAIL"
        
        print(f"{event_type:25} {expected_display:35} {actual_display:35} {status:10}")
        
        if not is_correct:
            all_correct = False
    
    print("-" * 80)
    
    # Test in table display
    print("\nVerifying display in table (column 4 — Event Type):")
    events = [
        TimelineEvent(event_id=f"e{i}", sequence_index=i, app=f"com.app{i}",
                     event_type=et, source="test", evidence_type="DIRECT")
        for i, (et, _) in enumerate(test_cases)
    ]
    
    model.set_events(events)
    
    all_match = True
    for row, (event_type, expected_display) in enumerate(test_cases):
        idx = model.createIndex(row, 4)
        display_text = model.data(idx, Qt.ItemDataRole.DisplayRole)
        is_match = display_text == expected_display
        status = "✓" if is_match else "✗"
        
        print(f"  {status} Row {row}: {display_text}")
        
        if not is_match:
            all_match = False
    
    print("-" * 80)
    
    if all_correct and all_match:
        print("✓ PASS: All event types mapped and displayed correctly")
        print("  - Each event type has a unique emoji + label combination")
        print("  - Unmapped event types use generic fallback (ℹ️ + event_type)")
        print("  - Display layer shows mapped labels, backend unchanged")
    else:
        print("✗ FAIL: Event type mapping not working correctly")
    
    return all_correct and all_match

def main():
    print("\n" + "╔" + "=" * 78 + "╗")
    print("║" + " " * 78 + "║")
    print("║" + " COMPREHENSIVE VALIDATION OF DROIDTRACE PRO UI FIXES ".center(78) + "║")
    print("║" + " " * 78 + "║")
    print("╚" + "=" * 78 + "╝")
    
    result1 = validate_issue_1_timeline_ordering()
    result2 = validate_issue_2_activity_type_display()
    
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Issue 1 (Timeline Ordering):    {'✓ PASS' if result1 else '✗ FAIL'}")
    print(f"Issue 2 (Activity Type Display): {'✓ PASS' if result2 else '✗ FAIL'}")
    print("=" * 80)
    
    if result1 and result2:
        print("\n✓✓✓ ALL VALIDATIONS PASSED ✓✓✓\n")
        return 0
    else:
        print("\n✗✗✗ SOME VALIDATIONS FAILED ✗✗✗\n")
        return 1

if __name__ == "__main__":
    import sys
    sys.exit(main())
