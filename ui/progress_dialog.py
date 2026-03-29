"""
ui/progress_dialog.py
=======================
Non-blocking extraction progress dialog.

Shows a progress bar and live status log while the background extraction
worker runs.  The dialog is modal but the UI remains responsive because
all heavy work runs in a QThread.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar,
    QTextEdit, QPushButton, QHBoxLayout,
)

from utils.logger import get_logger

log = get_logger(__name__)


class ProgressDialog(QDialog):
    """
    Modal progress dialog for the extraction pipeline.

    Usage:
        dlg = ProgressDialog(parent=main_window)
        dlg.show()
        # from worker thread, emit signals → call append_log / set_progress
        dlg.mark_complete()
    """

    def __init__(self, parent=None, title: str = "Extracting…") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setMinimumHeight(340)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Stage label
        self._stage_label = QLabel("Initialising…")
        self._stage_label.setStyleSheet("font-size:13px; font-weight:600;")
        layout.addWidget(self._stage_label)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setFixedHeight(22)
        layout.addWidget(self._progress)

        # Log output
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setStyleSheet(
            "background:#0f1117; color:#8892a4; "
            "font-family:'Cascadia Code',monospace; font-size:11px; "
            "border:1px solid #2e344a; border-radius:6px;"
        )
        layout.addWidget(self._log)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._close_btn = QPushButton("Close")
        self._close_btn.setEnabled(False)
        self._close_btn.setFixedHeight(32)
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

    @pyqtSlot(int, str)
    def set_progress(self, value: int, stage: str) -> None:
        """Update progress bar and stage label."""
        self._progress.setValue(min(max(value, 0), 100))
        self._stage_label.setText(stage)

    @pyqtSlot(str)
    def append_log(self, message: str) -> None:
        """Append a status message to the log area."""
        self._log.append(f"  {message}")

    @pyqtSlot()
    def mark_complete(self) -> None:
        """Mark extraction as complete and enable the Close button."""
        self._progress.setValue(100)
        self._stage_label.setText("✅  Extraction complete")
        self._stage_label.setStyleSheet(
            "font-size:13px; font-weight:600; color:#22c55e;"
        )
        self._close_btn.setEnabled(True)
        self._close_btn.setObjectName("primaryBtn")

    @pyqtSlot(str)
    def mark_error(self, error: str) -> None:
        """Mark extraction as failed."""
        self._stage_label.setText(f"❌  Error: {error[:80]}")
        self._stage_label.setStyleSheet(
            "font-size:13px; font-weight:600; color:#ef4444;"
        )
        self._close_btn.setEnabled(True)
