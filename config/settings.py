"""
config/settings.py
==================
Central configuration file for DroidTrace Pro.
All tunable thresholds, paths, and constants live here.
Never scatter magic numbers across the codebase.
"""

import sys
from pathlib import Path

# ─────────────────────────────────────────────
# ⚖️ Forensic Engine Configuration
# ─────────────────────────────────────────────

# BUNDLE_ROOT: Where code and bundled assets (like adb.exe) live.
# In a PyInstaller bundle, this is a temporary extraction folder (_MEIPASS).
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    BUNDLE_ROOT = Path(sys._MEIPASS)
else:
    BUNDLE_ROOT = Path(__file__).resolve().parent.parent

# USER_ROOT: Where logs and reports should be written to stay persistent.
# This points to the ACTUAL directory where the .exe resides on the user's disk.
if getattr(sys, 'frozen', False):
    USER_ROOT = Path(sys.executable).parent
else:
    # During dev, write to the project root
    USER_ROOT = BUNDLE_ROOT

# ─────────────────────────────────────────────
# ADB
# ─────────────────────────────────────────────
# ADB binary should be bundled inside the EXE
ADB_BUNDLED_PATH = BUNDLE_ROOT / "assets" / "adb" / "adb.exe"

# How long (seconds) to wait for a single ADB command before timeout
ADB_COMMAND_TIMEOUT: int = 30

# How long (seconds) to wait for device to appear on 'adb wait-for-device'
ADB_DEVICE_WAIT_TIMEOUT: int = 10

# Max retries for transient ADB failures
ADB_MAX_RETRIES: int = 3

# Delay (seconds) between retry attempts — exponential backoff base
ADB_RETRY_BACKOFF_BASE: float = 1.5

# ─────────────────────────────────────────────
# Data Collection
# ─────────────────────────────────────────────
# Maximum bytes to read from a single ADB command output (10 MB safety cap)
MAX_OUTPUT_BYTES: int = 10 * 1024 * 1024

# ─────────────────────────────────────────────
# Timeline Engine
# ─────────────────────────────────────────────
# Events within this window (seconds) are candidates for deduplication
DEDUP_WINDOW_SECONDS: int = 2

# ─────────────────────────────────────────────
# Correlation Engine
# ─────────────────────────────────────────────
# Max gap (seconds) between two events to be considered "correlated"
CORRELATION_WINDOW_SECONDS: int = 60

# ─────────────────────────────────────────────
# Inference / Pattern Detection
# ─────────────────────────────────────────────
# Hours considered "night" for late-night activity inference
NIGHT_HOURS_START: int = 0   # 00:00
NIGHT_HOURS_END: int = 5     # 05:00

# Number of events in a time window to trigger "communication burst" flag
BURST_EVENT_THRESHOLD: int = 5
BURST_WINDOW_SECONDS: int = 600  # 10 minutes

# Seconds after app install before foreground use triggers "immediate use" flag
IMMEDIATE_USE_THRESHOLD_SECONDS: int = 60

# Seconds for airplane-mode-toggle detection (wifi off → wifi on fast)
AIRPLANE_TOGGLE_THRESHOLD_SECONDS: int = 30

# Hours of silence that constitute an "activity blackout"
BLACKOUT_THRESHOLD_HOURS: int = 6

# ─────────────────────────────────────────────
# Logging (Persistent Output → USER_ROOT)
# ─────────────────────────────────────────────
LOG_DIR = USER_ROOT / "logs"
LOG_FILE_NAME = "droidtrace.log"
LOG_MAX_BYTES: int = 5 * 1024 * 1024   # 5 MB per log file
LOG_BACKUP_COUNT: int = 3

# ─────────────────────────────────────────────
# 🛡️ Forensic Safety Configuration
# ─────────────────────────────────────────────
# Packages starting with these prefixes are considered 'system apps'
# and are hidden from most forensic analysis views to reduce noise.
SAFE_PREFIXES = (
    "com.android", "com.google", "com.samsung",
    "com.sec", "com.qualcomm", "com.miui",
    "com.oppo", "com.vivo", "com.huawei",
    "android", "vendor."
)

# ─────────────────────────────────────────────
# Reporting (Persistent Output → USER_ROOT)
# ─────────────────────────────────────────────
REPORT_OUTPUT_DIR = USER_ROOT / "reports"
# Template is a bundled asset
REPORT_TEMPLATE = BUNDLE_ROOT / "core" / "reporting" / "templates" / "report.html"
