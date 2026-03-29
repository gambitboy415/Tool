
import sys
import unittest
from datetime import datetime
from PyQt6.QtWidgets import QApplication
from ui.analysis_panel import AnalysisPanel
from models.timeline_event import TimelineEvent

# Mocking the models if necessary, but TimelineEvent is just a dataclass usually.
# Let's assume TimelineEvent is available.

class TestSuspiciousFilter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication(sys.argv)

    def test_filter_logic(self):
        panel = AnalysisPanel()
        
        # 1. Create a system app event
        system_app = "com.android.settings"
        system_event = TimelineEvent(
            timestamp=datetime(2023, 1, 1, 10, 0),
            event_type="APP_OPENED",
            app=system_app,
            source="usage_stats",
            severity="NORMAL",
            flags=["HEAVY_USAGE"] # Flagged but should be filtered
        )
        
        # 2. Create a brand system app
        samsung_app = "com.samsung.android.knox.containercore"
        samsung_event = TimelineEvent(
            timestamp=datetime(2023, 1, 1, 10, 1),
            event_type="APP_INSTALLED",
            app=samsung_app,
            source="package_detail",
            severity="NORMAL",
            raw_fields={"apk_location": "system"} # Explicit system marker
        )
        
        # 3. Create a truly suspicious app
        malware_app = "com.evil.spyware"
        malware_event = TimelineEvent(
            timestamp=datetime(2023, 1, 1, 10, 5),
            event_type="APP_OPENED",
            app=malware_app,
            source="usage_stats",
            severity="SUSPICIOUS",
            flags=["SUSPICIOUS"]
        )
        
        # 4. Create an overlay (automatically filtered)
        overlay_app = "com.test.overlay.theme"
        overlay_event = TimelineEvent(
            timestamp=datetime(2023, 1, 1, 10, 6),
            event_type="APP_INSTALLED",
            app=overlay_app,
            source="package_detail",
            flags=["HEAVY_USAGE"]
        )

        timeline = [system_event, samsung_event, malware_event, overlay_event]
        
        # Mocking the internal stats update to see counts
        panel.update_analysis(timeline)
        
        suspicious_count = int(panel._stat_labels["suspicious"].text())
        
        print(f"Suspicious Count: {suspicious_count}")
        
        # We expect ONLY 'com.evil.spyware' to remain.
        # com.android.settings -> Filtered by prefix
        # com.samsung... -> Filtered by prefix and apk_location
        # com.test.overlay.theme -> Filtered by pattern 'overlay'
        
        self.assertEqual(suspicious_count, 1)
        
    def test_heuristic_first_install(self):
        panel = AnalysisPanel()
        
        # Earliest install time across timeline
        earliest_ts = "2020-01-01 00:00:00"
        
        # App installed at base manufacturing time
        oem_bloat = "com.oem.bloatware"
        bloat_event = TimelineEvent(
            timestamp=datetime(2023, 1, 1, 10, 0),
            event_type="APP_INSTALLED",
            app=oem_bloat,
            source="package_detail",
            flags=["DORMANT_APP"],
            raw_fields={"firstInstallTime": earliest_ts}
        )
        
        # User app installed later
        user_app = "com.user.game"
        user_event = TimelineEvent(
            timestamp=datetime(2023, 1, 1, 11, 0),
            event_type="APP_INSTALLED",
            app=user_app,
            source="package_detail",
            flags=["HEAVY_USAGE"],
            raw_fields={"firstInstallTime": "2023-01-01 11:00:00"}
        )
        
        timeline = [bloat_event, user_event]
        panel.update_analysis(timeline)
        
        suspicious_count = int(panel._stat_labels["suspicious"].text())
        print(f"Heuristic Suspicious Count: {suspicious_count}")
        
        # Expect 1 (User app only, bloatware filtered by manufacture time)
        self.assertEqual(suspicious_count, 1)

if __name__ == "__main__":
    unittest.main()
