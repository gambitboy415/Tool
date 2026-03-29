import sys
import os
from unittest.mock import MagicMock, patch

# Add current directory to path
sys.path.append(os.getcwd())

from core.adb.adb_connector import AdbConnector, DeviceInfo

def test_get_device_info_logic():
    print("Testing AdbConnector.get_device_info() core logic...")
    
    # Mock AdbConnector and its shell method
    connector = AdbConnector(serial="test-123")
    connector._connected = True
    
    def side_effect(cmd):
        if "getprop" in cmd:
            if "ro.product.model" in cmd: return "Test Model"
            if "ro.product.manufacturer" in cmd: return "Test Corp"
            if "ro.build.version.release" in cmd: return "13"
            if "ro.build.version.sdk" in cmd: return "33"
            if "ro.build.fingerprint" in cmd: return "test/fingerprint"
            return ""
        if "date" in cmd:
            return "1672567200\n+0530" # Fake device time
        return ""
    
    with patch.object(AdbConnector, 'shell', side_effect=side_effect):
        info = connector.get_device_info()
        
        print(f"  Model: {info.model}")
        print(f"  SDK:   {info.sdk_version}")
        print(f"  Time:  {info.device_time_utc}")
        print(f"  Offset: {info.timezone_offset_sec}")
        
        assert info.model == "Test Model"
        assert info.sdk_version == 33
        assert info.timezone_offset_sec == 19800 # +05:30
        
    print("  PASS: Device properties and clock sync data extracted correctly.")

if __name__ == "__main__":
    try:
        test_get_device_info_logic()
        print("-" * 30)
        print("CONNECTOR INFO TEST PASSED!")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"TEST FAILED: {e}")
        sys.exit(1)
