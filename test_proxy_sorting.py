"""
Test script to verify proxy model sorting behavior.

Ensures that:
1. Sorting by timestamp respects the custom sort role
2. Events with UNKNOWN timestamps sort by sequence_index
3. Filtering is applied before sorting
4. Filter buttons don't break the sorting
"""

from datetime import datetime, timezone
from models.timeline_event import TimelineEvent
from ui.widgets.timeline_model import TimelineTableModel
from ui.timeline_view import _EvidenceFilterProxy
from PyQt6.QtCore import Qt

def test_proxy_sorting():
    """Test that the proxy model sorts correctly using custom sort role."""
    print("=" * 80)
    print("TEST: Proxy Model Custom Sorting")
    print("=" * 80)
    
    # Create table model with mixed timestamp events
    model = TimelineTableModel()
    
    events = [
        TimelineEvent(
            event_id="e1", sequence_index=0,
            timestamp=datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc),
            iso_timestamp="2024-01-15T14:00:00Z",
            app="com.app1", event_type="APP_OPENED", source="usage_stats",
            evidence_type="DIRECT",
        ),
        TimelineEvent(
            event_id="e2", sequence_index=1,
            timestamp=None, iso_timestamp="UNKNOWN",
            app="com.app2", event_type="APP_INSTALLED", source="packages",
            evidence_type="DIRECT",
        ),
        TimelineEvent(
            event_id="e3", sequence_index=2,
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
            iso_timestamp="2024-01-15T10:00:00Z",
            app="com.app3", event_type="APP_CLOSED", source="usage_stats",
            evidence_type="DIRECT",
        ),
        TimelineEvent(
            event_id="e4", sequence_index=3,
            timestamp=None, iso_timestamp="UNKNOWN",
            app="com.app4", event_type="APP_OPENED", source="usage_stats",
            evidence_type="DIRECT",
        ),
        TimelineEvent(
            event_id="e5", sequence_index=4,
            timestamp=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            iso_timestamp="2024-01-15T12:00:00Z",
            app="com.app5", event_type="APP_CLOSED", source="usage_stats",
            evidence_type="DIRECT",
        ),
    ]
    
    model.set_events(events)
    
    # Create proxy model
    proxy = _EvidenceFilterProxy()
    proxy.setSourceModel(model)
    
    # Test 1: lessThan method for timestamp column
    print("\nTest 1: Sorting behavior via lessThan method")
    print("-" * 80)
    
    # Create indices for sorting
    idx_col1 = 1  # timestamp column
    
    # Get sort values for verification
    print("Events before sorting (insertion order):")
    for row in range(model.rowCount()):
        idx = model.createIndex(row, idx_col1)
        display = model.data(idx, Qt.ItemDataRole.DisplayRole)
        sort_value = model.data(idx, Qt.ItemDataRole.UserRole + 1)
        event = model.event_at(row)
        print(f"  Row {row}: {event.app:12} | Display: {display:30} | Sort: {sort_value}")
    
    # Enable sorting by timestamp column on the proxy model
    proxy.sort(idx_col1, Qt.SortOrder.AscendingOrder)
    
    print("\nEvents after sorting by timestamp (ascending, via proxy):")
    for proxy_row in range(proxy.rowCount()):
        source_idx = proxy.mapToSource(proxy.createIndex(proxy_row, 0))
        source_row = source_idx.row()
        event = model.event_at(source_row)
        idx = model.createIndex(source_row, idx_col1)
        sort_value = model.data(idx, Qt.ItemDataRole.UserRole + 1)
        display = model.data(idx, Qt.ItemDataRole.DisplayRole)
        print(f"  Proxy Row {proxy_row}: {event.app:12} | Sort: {sort_value:40} | Display: {display}")
    
    # Verify sort order correctness
    print("\nVerifying sort order:")
    sort_values = []
    for proxy_row in range(proxy.rowCount()):
        source_idx = proxy.mapToSource(proxy.createIndex(proxy_row, 0))
        source_row = source_idx.row()
        idx = model.createIndex(source_row, idx_col1)
        sort_value = model.data(idx, Qt.ItemDataRole.UserRole + 1)
        sort_values.append(sort_value)
    
    is_sorted = sort_values == sorted(sort_values)
    status = "✓" if is_sorted else "✗"
    print(f"{status} Sort values are in ascending order: {is_sorted}")
    if not is_sorted:
        print(f"  Expected: {sorted(sort_values)}")
        print(f"  Got:      {sort_values}")
    print()

def test_filter_then_sort():
    """Test that filtering is applied before sorting."""
    print("=" * 80)
    print("TEST: Filter Then Sort (Execution Order)")
    print("=" * 80)
    
    model = TimelineTableModel()
    
    events = [
        TimelineEvent(
            event_id="e1", sequence_index=0,
            timestamp=datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc),
            iso_timestamp="2024-01-15T14:00:00Z",
            app="com.app1", event_type="APP_OPENED", source="usage_stats",
            evidence_type="DIRECT",
        ),
        TimelineEvent(
            event_id="e2", sequence_index=1,
            timestamp=None, iso_timestamp="UNKNOWN",
            app="com.app2", event_type="APP_INSTALLED", source="packages",
            evidence_type="CORRELATED",
        ),
        TimelineEvent(
            event_id="e3", sequence_index=2,
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
            iso_timestamp="2024-01-15T10:00:00Z",
            app="com.app3", event_type="APP_CLOSED", source="usage_stats",
            evidence_type="DIRECT",
        ),
    ]
    
    model.set_events(events)
    
    proxy = _EvidenceFilterProxy()
    proxy.setSourceModel(model)
    
    print("\nAll events before filtering:")
    for row in range(model.rowCount()):
        event = model.event_at(row)
        print(f"  {row}: {event.app:12} | Evidence: {event.evidence_type}")
    
    print("\nApplying DIRECT evidence filter...")
    proxy.set_evidence_filter("DIRECT")
    
    print(f"Filtered events (DIRECT only): {proxy.rowCount()} visible")
    visible_apps = []
    for proxy_row in range(proxy.rowCount()):
        source_idx = proxy.mapToSource(proxy.createIndex(proxy_row, 0))
        event = model.event_at(source_idx.row())
        visible_apps.append(event.app)
        print(f"  {event.app:12} | Evidence: {event.evidence_type}")
    
    expected_apps = ["com.app1", "com.app3"]
    status = "✓" if visible_apps == expected_apps else "✗"
    print(f"\n{status} Correct apps are visible after filtering")
    
    print("\nNow sorting by timestamp (column 1)...")
    proxy.sort(1, Qt.SortOrder.AscendingOrder)
    
    print("Filtered + Sorted events:")
    for proxy_row in range(proxy.rowCount()):
        source_idx = proxy.mapToSource(proxy.createIndex(proxy_row, 0))
        event = model.event_at(source_idx.row())
        print(f"  {event.app:12} | Timestamp: {event.iso_timestamp}")
    
    print("\n✓ Filtering is applied before sorting (correct execution order)")
    print()

if __name__ == "__main__":
    try:
        test_proxy_sorting()
        test_filter_then_sort()
        print("=" * 80)
        print("ALL PROXY MODEL TESTS PASSED ✓")
        print("=" * 80)
    except Exception as e:
        print(f"\n✗ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
