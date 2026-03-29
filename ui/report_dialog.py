"""
ui/report_dialog.py
=====================
Report export options dialog.

Allows the investigator to choose:
  - Report format (HTML / JSON)
  - Output directory
  - Whether to auto-open the report after generation

Emits:
  export_requested(format: str, output_dir: str)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QRadioButton, QButtonGroup,
    QLineEdit, QFileDialog, QGroupBox, QCheckBox,
)

from config.settings import REPORT_OUTPUT_DIR
from utils.logger import get_logger

log = get_logger(__name__)


class ReportDialog(QDialog):
    """
    Report export configuration dialog.

    Signals:
        export_requested(str, str):  (format, output_directory)
    """

    export_requested = pyqtSignal(str, str, bool, bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export Forensic Report")
        self.setModal(True)
        self.setFixedWidth(480)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        # ── Format ──────────────────────────────────────────────────────────
        fmt_group = QGroupBox("Report Format")
        fmt_layout = QVBoxLayout(fmt_group)
        self._fmt_group = QButtonGroup(self)

        self._html_radio = QRadioButton("📄  HTML  — Self-contained, dark-theme, offline viewable")
        self._html_radio.setChecked(True)
        self._json_radio = QRadioButton("📦  JSON  — Machine-readable, for external processing")

        self._fmt_group.addButton(self._html_radio, 0)
        self._fmt_group.addButton(self._json_radio, 1)
        fmt_layout.addWidget(self._html_radio)
        fmt_layout.addWidget(self._json_radio)
        layout.addWidget(fmt_group)

        # ── Output directory ─────────────────────────────────────────────────
        dir_group = QGroupBox("Output Directory")
        dir_layout = QHBoxLayout(dir_group)
        self._dir_edit = QLineEdit(str(REPORT_OUTPUT_DIR))
        self._dir_edit.setPlaceholderText("Select output directory…")
        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse)
        dir_layout.addWidget(self._dir_edit)
        dir_layout.addWidget(browse_btn)
        layout.addWidget(dir_group)

        # ── Options ─────────────────────────────────────────────────────────
        opts_group = QGroupBox("Report Filters")
        opts_layout = QVBoxLayout(opts_group)
        
        self._only_suspicious_cb = QCheckBox("Only export Suspicious/Important events")
        self._include_behavioral_cb = QCheckBox("Include Behavioral Summary Profile")
        self._include_behavioral_cb.setChecked(True)
        self._auto_open_cb = QCheckBox("Open report automatically after export")
        self._auto_open_cb.setChecked(True)

        opts_layout.addWidget(self._only_suspicious_cb)
        opts_layout.addWidget(self._include_behavioral_cb)
        opts_layout.addWidget(self._auto_open_cb)
        layout.addWidget(opts_group)

        # ── Buttons ─────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedHeight(34)
        cancel_btn.clicked.connect(self.reject)
        self._export_btn = QPushButton("📄  Export Report")
        self._export_btn.setObjectName("primaryBtn")
        self._export_btn.setFixedHeight(34)
        self._export_btn.clicked.connect(self._on_export)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self._export_btn)
        layout.addLayout(btn_row)

    def _browse(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Select Output Directory", self._dir_edit.text()
        )
        if directory:
            self._dir_edit.setText(directory)

    def _on_export(self) -> None:
        fmt = "html" if self._html_radio.isChecked() else "json"
        out_dir = self._dir_edit.text().strip() or str(REPORT_OUTPUT_DIR)
        only_suspicious = self._only_suspicious_cb.isChecked()
        include_behavioral = self._include_behavioral_cb.isChecked()
        self.export_requested.emit(fmt, out_dir, only_suspicious, include_behavioral)
        self.accept()

    @property
    def auto_open(self) -> bool:
        return self._auto_open_cb.isChecked()
