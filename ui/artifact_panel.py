"""
ui/artifact_panel.py
======================
Artifact source selection panel.

Lets the investigator choose which artifact categories to collect before
starting an extraction run.  Also houses the "Extract" button that triggers
the full collection → parse → normalize → timeline → correlate → infer pipeline.

Emits:
  extraction_requested(dict)  — dict of {artifact_key: bool} + options
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QCheckBox,
    QPushButton, QLabel, QFrame, QSizePolicy, QHBoxLayout,
)

from utils.logger import get_logger

log = get_logger(__name__)

# Artifact definitions: (key, display label, description, default state)
_ARTIFACTS = [
    ("usage_stats",      "📊 Usage Stats",     "App foreground/background events (dumpsys usagestats)",     True),
    ("installed_apps",   "📦 Installed Apps",  "Full package inventory (pm list packages -f)",              True),
    ("uninstalled_apps", "🗑  Uninstalled Apps","Packages with residual data (pm list packages -u)",         True),
    ("network_stats",   "🌐 Network Stats",   "Per-app byte transfers (dumpsys netstats)",                  True),
    ("package_details",  "🔍 Package Details", "Per-package install time, permissions (dumpsys package)",   False),
    ("screen_state",     "🖥  Screen State",    "Screen on/off and wake locks (dumpsys power)",             True),
]

_OPTION_DEDUP      = ("dedup",        "Deduplicate near-identical events",       True)
_OPTION_NOISE      = ("remove_noise", "Filter OS background noise events",        True)
_OPTION_NIGHT_FLAG = ("night_flag",   "Flag late-night activity (00:00–05:00)",   True)


class ArtifactPanel(QWidget):
    """
    Artifact selection & extraction control panel.

    Signals:
        extraction_requested(dict): Emitted when the user clicks Extract.
            Payload example:
            {
                "usage_stats": True,
                "installed_apps": True,
                "package_details": False,
                "screen_state": True,
                "uninstalled_apps": True,
                "dedup": True,
                "remove_noise": True,
                "night_flag": True,
            }
    """

    extraction_requested = pyqtSignal(dict)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._artifact_checks: dict[str, QCheckBox] = {}
        self._option_checks:   dict[str, QCheckBox] = {}
        self._build_ui()
        self.setEnabled(False)   # disabled until device is connected

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # ── Artifact sources ─────────────────────────────────────────────────
        art_group = QGroupBox("Artifact Sources")
        art_layout = QVBoxLayout(art_group)
        art_layout.setSpacing(6)

        for key, label, tooltip, default in _ARTIFACTS:
            cb = QCheckBox(label)
            cb.setChecked(default)
            cb.setToolTip(tooltip)
            art_layout.addWidget(cb)
            self._artifact_checks[key] = cb

        # Select all / none shortcuts
        toggle_row = QHBoxLayout()
        all_btn  = QPushButton("Select All")
        none_btn = QPushButton("Clear All")
        all_btn.setFixedHeight(26)
        none_btn.setFixedHeight(26)
        all_btn.setObjectName("smallBtn")
        none_btn.setObjectName("smallBtn")
        all_btn.clicked.connect(lambda: self._toggle_all(True))
        none_btn.clicked.connect(lambda: self._toggle_all(False))
        toggle_row.addWidget(all_btn)
        toggle_row.addWidget(none_btn)
        toggle_row.addStretch()
        art_layout.addLayout(toggle_row)

        layout.addWidget(art_group)

        # ── Analysis options ─────────────────────────────────────────────────
        opt_group = QGroupBox("Analysis Options")
        opt_layout = QVBoxLayout(opt_group)
        opt_layout.setSpacing(6)

        for key, label, default in [_OPTION_DEDUP, _OPTION_NOISE, _OPTION_NIGHT_FLAG]:
            cb = QCheckBox(label)
            cb.setChecked(default)
            opt_layout.addWidget(cb)
            self._option_checks[key] = cb

        layout.addWidget(opt_group)

        # ── Extract button ───────────────────────────────────────────────────
        self._extract_btn = QPushButton("⚙  Extract & Analyse")
        self._extract_btn.setObjectName("primaryBtn")
        self._extract_btn.setFixedHeight(40)
        self._extract_btn.clicked.connect(self._on_extract)
        layout.addWidget(self._extract_btn)

        layout.addStretch()

    def _toggle_all(self, state: bool) -> None:
        for cb in self._artifact_checks.values():
            cb.setChecked(state)

    def _on_extract(self) -> None:
        payload: dict = {}
        for key, cb in self._artifact_checks.items():
            payload[key] = cb.isChecked()
        for key, cb in self._option_checks.items():
            payload[key] = cb.isChecked()
        log.info("Extraction requested: %s", payload)
        self.extraction_requested.emit(payload)

    def set_extracting(self, active: bool) -> None:
        """Disable controls during an extraction run."""
        self._extract_btn.setEnabled(not active)
        self._extract_btn.setText("⏳  Extracting…" if active else "⚙  Extract & Analyse")
