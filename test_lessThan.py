"""
Simple test to verify lessThan method is being invoked correctly.
"""

from datetime import datetime, timezone
from models.timeline_event import TimelineEvent
from ui.widgets.timeline_model import TimelineTableModel
from ui.timeline_view import _EvidenceFilterProxy
from PyQt6.QtCore import Qt, QModelIndex

def test_lessThan_method():
    """Test the lessThan method directly."""
    print("=" * 80)
    print("TEST: lessThan Method")
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
            evidence_type="DIRECT",
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
    
    # Test lessThan directly
    print("\nTesting lessThan method directly (column 1 = timestamp):")
    
    # Create indices
    left_idx = model.createIndex(0, 1)   # com.app1 with valid timestamp
    right_idx = model.createIndex(1, 1)  # com.app2 with UNKNOWN timestamp
    
    # Get sort values
    left_sort = model.data(left_idx, Qt.ItemDataRole.UserRole + 1)
    right_sort = model.data(right_idx, Qt.ItemDataRole.UserRole + 1)
    
    print(f"\nComparing rows 0 and 1 (timestamp column):")
    print(f"  Left  (e1, valid):   {left_sort}")
    print(f"  Right (e2, UNKNOWN): {right_sort}")
    
    # Call lessThan
    result = proxy.lessThan(left_idx, right_idx)
    print(f"  lessThan(left, right) = {result}")
    print(f"  Expected: True (valid timestamp < UNKNOWN_0000000001)")
    
    status = "✓" if result == True else "✗"
    print(f"  {status} lessThan result is correct")
    
    # Test with two UNKNOWN timestamps
    left_idx = model.createIndex(1, 1)   # com.app2 UNKNOWN (seq 1)
    right_idx = model.createIndex(2, 1)  # com.app3 valid (seq 2)
    
    left_sort = model.data(left_idx, Qt.ItemDataRole.UserRole + 1)
    right_sort = model.data(right_idx, Qt.ItemDataRole.UserRole + 1)
    
    print(f"\nComparing rows 1 and 2:")
    print(f"  Left  (e2, UNKNOWN): {left_sort}")
    print(f"  Right (e3, valid):   {right_sort}")
    
    result = proxy.lessThan(left_idx, right_idx)
    print(f"  lessThan(left, right) = {result}")
    print(f"  Expected: False (UNKNOWN_0000000001 > 2024-01-15T10:00:00Z alphabetically)")
    
    status = "✓" if result == False else "✗"
    print(f"  {status} lessThan result is correct")
    
    print("\n" + "=" * 80)
    print("lessThan tests completed ✓")
    print("=" * 80)

if __name__ == "__main__":
    try:
        test_lessThan_method()
    except Exception as e:
        print(f"\n✗ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
