import sys
import os
from datetime import datetime, timezone, timedelta

# Add current directory to path to import core modules
sys.path.append(os.getcwd())

from core.parsers.parser import _parse_timestamp
from models.raw_artifact import RawArtifact, ArtifactType

def test_clock_sync_offset():
    print("Testing Clock Sync Timezone Offset...")
    
    # +05:30 (IST) = 19800 seconds
    clock_sync = {
        "device_time_utc": "2023-01-01T12:00:00Z",
        "timezone_offset_sec": 19800,
        "host_drift_ms": 100
    }
    
    # Case 1: Naive Local Time String
    # If the phone says 12:00:00 and is in IST, UTC is 06:30:00
    local_ts = "2023-01-01 12:00:00"
    dt, approx = _parse_timestamp(local_ts, fallback=datetime.now(), clock_sync=clock_sync)
    
    expected_utc = datetime(2023, 1, 1, 6, 30, 0, tzinfo=timezone.utc)
    
    print(f"  Input: {local_ts}")
    print(f"  Result: {dt.isoformat()}")
    print(f"  Expected: {expected_utc.isoformat()}")
    
    assert dt == expected_utc, f"FAIL: Expected {expected_utc}, got {dt}"
    print("  PASS: Naive timestamp corrected by offset.")

def test_epoch_unaffected():
    print("Testing Epoch timestamps (should ignore offset)...")
    
    # 1672574400 = 2023-01-01 12:00:00 UTC
    epoch_ms = "1672574400000"
    clock_sync = {"timezone_offset_sec": 19800} # IST
    
    dt, approx = _parse_timestamp(epoch_ms, fallback=datetime.now(), clock_sync=clock_sync)
    
    expected_utc = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    
    print(f"  Input: {epoch_ms} (ms)")
    print(f"  Result: {dt.isoformat()}")
    
    assert dt == expected_utc, f"FAIL: Epoch should be absolute. Expected {expected_utc}, got {dt}"
    print("  PASS: Epoch remains absolute.")

if __name__ == "__main__":
    try:
        test_clock_sync_offset()
        print("-" * 30)
        test_epoch_unaffected()
        print("-" * 30)
        print("ALL CLOCK SYNC TESTS PASSED!")
    except Exception as e:
        print(f"TEST FAILED: {e}")
        sys.exit(1)
