import sys
import os
from datetime import datetime, timezone, timedelta

# Add current directory to path
sys.path.append(os.getcwd())

from models.timeline_event import TimelineEvent
from ui.widgets.timeline_model import TimelineTableModel

def test_ist_shift():
    print("Testing IST Timezone Shift (+05:30)...")
    
    # Create an event in UTC (12:00 UTC)
    utc_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    event = TimelineEvent(timestamp=utc_time)
    
    # Expected IST string: 2023-01-01 17:30:00 (12:00 + 5.5h)
    expected_ist = "2023-01-01 17:30:00"
    
    print(f"  Input (UTC): {utc_time.isoformat()}")
    print(f"  Result (IST): {event.iso_timestamp_ist}")
    print(f"  Expected:     {expected_ist}")
    
    assert event.iso_timestamp_ist == expected_ist, f"FAIL: Expected {expected_ist}, got {event.iso_timestamp_ist}"
    print("  PASS: Model correctly calculates IST from UTC.")

def test_ui_display():
    print("Testing UI Model Display Logic...")
    
    utc_time = datetime(2023, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    event = TimelineEvent(timestamp=utc_time, sequence_index=1)
    
    # The IST display for 10:00 UTC is 15:30 IST
    expected_display = "2023-01-01 15:30:00"
    
    # Test the static _display helper (using index 1 for Timestamp column)
    model = TimelineTableModel()
    display_text = model._display(event, 1)
    
    print(f"  Column 1 Text: {display_text}")
    print(f"  Expected IST:  {expected_display}")
    
    assert display_text == expected_display, f"FAIL: UI model did not show IST in timestamp column."
    print("  PASS: UI Model shows IST in table.")

if __name__ == "__main__":
    try:
        test_ist_shift()
        print("-" * 30)
        test_ui_display()
        print("-" * 30)
        print("ALL IST ALIGNMENT TESTS PASSED!")
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
LineContent: "        expected = datetime(2023, 1, 1, 5, 30, 0, tzinfo=timezone.utc)"
