"""
main.py
========
Entry point for DroidTrace Pro.

Bootstraps the Qt application, applies the global stylesheet, and launches
the main window.  Also initialises the logging subsystem before Qt starts
so early errors are captured.

Usage:
    python main.py
"""

from __future__ import annotations

import sys
import os
from pathlib import Path

# ── Ensure project root is on sys.path so all absolute imports resolve ────────
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from ui.main_window import MainWindow
from utils.logger import get_logger

log = get_logger("main")


def main() -> int:
    # ── High-DPI support (Windows) ────────────────────────────────────────────
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    app = QApplication(sys.argv)
    app.setApplicationName("DroidTrace Pro")
    app.setApplicationDisplayName("DroidTrace Pro")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("DroidTrace")

    # ── Default application font ──────────────────────────────────────────────
    font = QFont("Segoe UI", 10)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(font)

    log.info("Starting DroidTrace Pro v1.0.0")

    window = MainWindow()
    window.show()

    exit_code = app.exec()
    log.info("Application exited with code %d", exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
