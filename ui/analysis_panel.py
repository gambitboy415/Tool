"""
ui/analysis_panel.py
======================
Behavioral analysis & suspicious events panel.

Displays:
  - Summary counts (flags, INFERRED events, suspicious apps)
  - Per-flag breakdown with colour-coded indicators
  - List of all suspicious apps (clickable to filter timeline)
  - Scrollable INFERRED event log

Emits:
  app_filter_requested(str)  — package name to filter timeline view on
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QScrollArea, QFrame, QSizePolicy, QPushButton,
    QGroupBox, QGridLayout,
)

from core.inference.inference_engine import InferenceReport
from core.correlation.correlation_engine import CorrelationReport
from core.analysis.behavioral_summary import BehavioralSummary
from models.timeline_event import TimelineEvent
from utils.logger import get_logger

log = get_logger(__name__)

# Flag → emoji label
_FLAG_LABELS: dict[str, str] = {
    # Core Audit Flags
    "ACTIVITY_GAP":             "⏱️ Activity Gap (Blackout)",
    "TEMPORAL_INTEGRITY_INVALID": "❌ Temporal Integrity Failure",
    "SUSPICIOUS":               "🚩 Suspicious Behavioral Hit",
    
    # Intelligence Layer Markers
    "HEAVY_USAGE":              "⚡ Intense Activity Window",
    "APP_LIFECYCLE":            "🧬 App Lifecycle Confirmed",
    "DORMANT_APP":              "💤 Dormant Application",
    
    # Behavioral Indicators (from fusion rules)
    "LIFECYCLE_CORRELATED":     "🧬 Lifecycle Correlation",
    "SUSPICIOUS_REMOVAL":       "🚨 Suspicious App Removal",
    "BACKGROUND_ACTIVITY_DETECTED": "🌑 Background Activity Risk",
    
    # Deterministic Behavioral Rules (Intelligence Layer)
    "LATE_NIGHT_ACTIVITY":      "🌙 Late Night Activity",
    "IMMEDIATE_APP_USE":        "⚡ Immediate App Use",
    "COMMUNICATION_BURST":      "💬 Communication Burst",
    "SILENT_BACKGROUND_SERVICE": "🌑 Silent Background Service",
    "RAPID_INSTALL_UNINSTALL":  "🔄 Rapid Install/Uninstall",
    "ANTI_FORENSIC_SEQUENCE":   "🧹 Anti-Forensic Sequence",
    "FACTORY_RESET_INDICATOR":  "☣️ Factory Reset Indicator",
}

_FLAG_SEVERITY: dict[str, str] = {     # flag → colour hex
    "ACTIVITY_GAP":             "#f97316", # Orange
    "TEMPORAL_INTEGRITY_INVALID": "#ef4444", # Red
    "SUSPICIOUS":               "#ef4444", # Red
    "HEAVY_USAGE":              "#f97316", # Orange
    "APP_LIFECYCLE":            "#22c55e", # Green (Positive match)
    "DORMANT_APP":              "#8892a4", # Gray
    "LIFECYCLE_CORRELATED":     "#6366f1", # Indigo
    "SUSPICIOUS_REMOVAL":       "#ef4444", # Red
    "DORMANT_APP":              "#8892a4", # Muted
    "LATE_NIGHT_ACTIVITY":      "#f97316", # Orange
    "IMMEDIATE_APP_USE":        "#ef4444", # Red
    "COMMUNICATION_BURST":      "#3b82f6", # Blue
    "SILENT_BACKGROUND_SERVICE": "#f97316", # Orange
    "RAPID_INSTALL_UNINSTALL":  "#ef4444", # Red
    "ANTI_FORENSIC_SEQUENCE":   "#ef4444", # Red
    "FACTORY_RESET_INDICATOR":  "#ef4444", # Red
}


class AnalysisPanel(QWidget):
    """
    Right-side behavioral analysis panel.

    Signals:
        app_filter_requested(str):  Emitted when user clicks a suspicious app name.
    """

    app_filter_requested = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # ── Summary stats row ────────────────────────────────────────────────
        stats_group = QGroupBox("Analysis Summary")
        stats_layout = QGridLayout(stats_group)

        self._stat_labels: dict[str, QLabel] = {}
        stats = [
            ("flagged",   "Flagged Events",    0, 0),
            ("inferred",  "Inferred Detections",0, 1),
            ("correlated","Correlated Pairs",  1, 0),
            ("suspicious","Suspicious Apps",   1, 1),
        ]
        for key, label, row, col in stats:
            frame = QFrame()
            fl = QVBoxLayout(frame)
            fl.setContentsMargins(8, 8, 8, 8)
            frame.setStyleSheet("background:#1a1d27; border-radius:6px;")
            val = QLabel("—")
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val.setStyleSheet("font-size:22px; font-weight:700; color:#6366f1;")
            lbl = QLabel(label)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("font-size:10px; color:#8892a4;")
            fl.addWidget(val)
            fl.addWidget(lbl)
            stats_layout.addWidget(frame, row, col)
            self._stat_labels[key] = val

        layout.addWidget(stats_group)

        # ── Scroll area for everything else ──────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(12)
        scroll_layout.setContentsMargins(0, 0, 4, 0)

        # Flags breakdown
        self._flags_group = QGroupBox("Behavioral Flags Detected")
        self._flags_layout = QVBoxLayout(self._flags_group)
        self._flags_layout.setSpacing(4)
        scroll_layout.addWidget(self._flags_group)

        # Suspicious apps
        self._apps_group = QGroupBox("Suspicious Applications")
        self._apps_layout = QVBoxLayout(self._apps_group)
        self._apps_layout.setSpacing(4)
        scroll_layout.addWidget(self._apps_group)

        # Behavioral Profile
        self._profile_group = QGroupBox("Behavioral Profile")
        self._profile_layout = QVBoxLayout(self._profile_group)
        self._profile_layout.setSpacing(8)
        scroll_layout.addWidget(self._profile_group)

        # Sessions View
        self._sessions_group = QGroupBox("📊 User Activity Sessions")
        self._sessions_layout = QVBoxLayout(self._sessions_group)
        self._sessions_layout.setSpacing(4)
        scroll_layout.addWidget(self._sessions_group)

        # Forensic Correlations
        self._correlations_group = QGroupBox("🧬 Forensic Correlation Log")
        self._correlations_layout = QVBoxLayout(self._correlations_group)
        self._correlations_layout.setSpacing(4)
        scroll_layout.addWidget(self._correlations_group)

        # INFERRED detections log
        self._inferred_group = QGroupBox("🔍 Inferred Detections")
        self._inferred_layout = QVBoxLayout(self._inferred_group)
        self._inferred_layout.setSpacing(4)
        scroll_layout.addWidget(self._inferred_group)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)

    # ── Public update API ──────────────────────────────────────────────────────

    def update_analysis(
        self,
        timeline: list[TimelineEvent],
        inference_report: Optional[InferenceReport] = None,
        correlation_report: Optional[CorrelationReport] = None,
        behavioral_summary: Optional[BehavioralSummary] = None,
    ) -> None:
        """
        Populate the panel from a completed analysis session.

        Args:
            timeline:           Full processed TimelineEvent list.
            inference_report:   Report from the InferenceEngine.
            correlation_report: Report from the CorrelationEngine.
        """
        flagged    = [e for e in timeline if e.flags]
        inferred   = [e for e in timeline if e.evidence_type == "INFERRED" and e.event_type != "APP_SESSION"]
        correlated = [e for e in timeline if e.evidence_type == "CORRELATED"]
        sessions   = [e for e in timeline if e.event_type == "APP_SESSION"]
        suspicious = sorted({e.app for e in flagged if e.app and e.app != "system"})

        # Update summary stats
        self._stat_labels["flagged"].setText(str(len(flagged)))
        self._stat_labels["inferred"].setText(str(len(inferred)))
        self._stat_labels["correlated"].setText(str(len(correlated)))
        self._stat_labels["suspicious"].setText(str(len(suspicious)))

        # Behavioral Profile
        self._clear_layout(self._profile_layout)
        if behavioral_summary:
            win_lbl = QLabel(f"Active Window:  <span style='color:#6366f1;font-weight:600;'>{behavioral_summary.to_dict()['active_window']}</span>")
            win_lbl.setTextFormat(Qt.TextFormat.RichText)
            
            dur_lbl = QLabel(f"Total Active Time: <span style='color:#6366f1;font-weight:600;'>{self._format_duration(behavioral_summary.total_active_duration.total_seconds())}</span>")
            dur_lbl.setTextFormat(Qt.TextFormat.RichText)
            
            sess_lbl = QLabel(f"Total Sessions: <span style='color:#6366f1;font-weight:600;'>{behavioral_summary.session_count}</span>")
            sess_lbl.setTextFormat(Qt.TextFormat.RichText)
            
            top_apps_title = QLabel("Top Applications by Duration:")
            top_apps_title.setStyleSheet("font-size:10px; color:#8892a4; margin-top:4px;")
            
            self._profile_layout.addWidget(win_lbl)
            self._profile_layout.addWidget(dur_lbl)
            self._profile_layout.addWidget(sess_lbl)
            self._profile_layout.addWidget(top_apps_title)
            
            for app, duration in behavioral_summary.top_apps_by_duration:
                app_row = QHBoxLayout()
                name = QLabel(app if len(app) < 25 else app[:22]+"...")
                name.setStyleSheet("font-size:11px; color:#e2e8f0;")
                
                # Simple CSS bar
                max_dur = behavioral_summary.top_apps_by_duration[0][1] or 1
                pct = int(duration / max_dur * 100)
                bar = QFrame()
                bar.setFixedHeight(6)
                bar.setFixedWidth(int(pct * 0.8)) # scaled
                bar.setStyleSheet("background:#6366f1; border-radius:3px;")
                
                dur = QLabel(self._format_duration(duration))
                dur.setStyleSheet("font-size:11px; color:#6366f1; font-weight:600;")
                dur.setAlignment(Qt.AlignmentFlag.AlignRight)
                
                app_row.addWidget(name)
                app_row.addStretch()
                app_row.addWidget(bar)
                app_row.addWidget(dur)
                row_widget = QWidget()
                row_widget.setLayout(app_row)
                self._profile_layout.addWidget(row_widget)
        else:
            self._profile_layout.addWidget(QLabel("No behavioral profile generated."))

        # Flags breakdown
        self._clear_layout(self._flags_layout)
        flag_summary: dict[str, int] = {}
        for e in flagged:
            for f in e.flags:
                flag_summary[f] = flag_summary.get(f, 0) + 1

        if flag_summary:
            for flag, count in sorted(flag_summary.items(), key=lambda x: -x[1]):
                self._add_flag_row(flag, count)
        else:
            self._flags_layout.addWidget(QLabel("No behavioral flags detected."))

        # Suspicious apps
        self._clear_layout(self._apps_layout)
        if suspicious:
            for app in suspicious:
                btn = QPushButton(f"  {app}")
                btn.setStyleSheet(
                    "text-align:left; color:#ef4444; background:#1a1d27; "
                    "border:1px solid #2e344a; border-radius:4px; padding:4px 8px;"
                )
                btn.setFixedHeight(28)
                btn.clicked.connect(lambda _, a=app: self.app_filter_requested.emit(a))
                self._apps_layout.addWidget(btn)
        else:
            self._apps_layout.addWidget(QLabel("No suspicious apps identified."))

        # User Activity Sessions
        self._clear_layout(self._sessions_layout)
        if sessions:
            for s in sessions:
                frame = QFrame()
                border_color = "#2e344a"
                if s.severity == "SUSPICIOUS":
                    border_color = "#ef4444"
                elif s.severity == "IMPORTANT":
                    border_color = "#f97316"
                    
                frame.setStyleSheet(
                    f"background:#1a1d27; border-left:3px solid {border_color}; "
                    "border-radius:4px; padding:6px;"
                )
                fl = QVBoxLayout(frame)
                fl.setContentsMargins(8, 4, 8, 4)
                
                header_layout = QHBoxLayout()
                header_layout.setContentsMargins(0, 0, 0, 0)
                app_lbl = QLabel(s.app)
                app_lbl.setStyleSheet(f"color:{border_color}; font-weight:600; font-size:11px;")
                dur_lbl = QLabel(self._format_duration(s.raw_fields.get("duration_sec", 0)))
                dur_lbl.setStyleSheet("color:#8892a4; font-size:11px; font-weight:600;")
                header_layout.addWidget(app_lbl)
                header_layout.addStretch()
                header_layout.addWidget(dur_lbl)
                
                header_widget = QWidget()
                header_widget.setLayout(header_layout)
                
                fl.addWidget(header_widget)
                
                stype = s.raw_fields.get("session_type", "EXACT")
                desc_text = f"Start: {s.iso_timestamp}"
                if stype != "EXACT":
                    desc_text += f" (Type: {stype})"
                    
                desc = QLabel(f"{desc_text}\n{s.reason}")
                desc.setStyleSheet("color:#8892a4; font-size:10px;")
                fl.addWidget(desc)
                
                self._sessions_layout.addWidget(frame)
        else:
            self._sessions_layout.addWidget(QLabel("No activity sessions reconstructed."))

        # Forensic Correlations
        self._clear_layout(self._correlations_layout)
        corr_events = [e for e in timeline if e.event_type in ("APP_LIFECYCLE", "HEAVY_USAGE", "ANTI_FORENSIC_SEQUENCE", "COMMUNICATION_BURST")]
        if corr_events:
            for e in corr_events:
                frame = QFrame()
                frame.setStyleSheet("background:#1a1d27; border-left:3px solid #6366f1; border-radius:4px; padding:6px;")
                fl = QVBoxLayout(frame)
                fl.setContentsMargins(8, 4, 8, 4)
                title = QLabel(f"🧬 {e.event_type} | {e.severity}")
                title.setStyleSheet("color:#6366f1; font-weight:700; font-size:11px;")
                desc = QLabel(f"{e.app}\n{e.description}")
                desc.setWordWrap(True)
                desc.setStyleSheet("color:#e2e8f0; font-size:11px;")
                reason = QLabel(f"Reason: {e.reason}")
                reason.setStyleSheet("color:#8892a4; font-size:10px; font-style:italic;")
                fl.addWidget(title)
                fl.addWidget(desc)
                fl.addWidget(reason)
                self._correlations_layout.addWidget(frame)
        else:
            self._correlations_layout.addWidget(QLabel("No high-level correlations found."))

        # INFERRED detections
        self._clear_layout(self._inferred_layout)
        other_inferred = [e for e in inferred if e not in corr_events]
        if other_inferred:
            for e in other_inferred:
                frame = QFrame()
                frame.setStyleSheet(
                    "background:#1a1d27; border-left:3px solid #a855f7; "
                    "border-radius:4px; padding:6px;"
                )
                fl = QVBoxLayout(frame)
                fl.setContentsMargins(8, 4, 8, 4)
                title = QLabel(f"🔍 {e.event_type}")
                title.setStyleSheet("color:#a855f7; font-weight:600; font-size:11px;")
                desc = QLabel(e.description)
                desc.setWordWrap(True)
                desc.setStyleSheet("color:#8892a4; font-size:11px;")
                ts = QLabel(e.iso_timestamp)
                ts.setStyleSheet("color:#6366f1; font-size:10px; font-family:monospace;")
                fl.addWidget(title)
                fl.addWidget(desc)
                fl.addWidget(ts)
                self._inferred_layout.addWidget(frame)
        else:
            self._inferred_layout.addWidget(QLabel("No other inferred events generated."))

        log.info(
            "AnalysisPanel updated: %d flagged, %d inferred, %d suspicious apps",
            len(flagged), len(inferred), len(suspicious),
        )

    def clear(self) -> None:
        """Reset the panel to its empty state."""
        for lbl in self._stat_labels.values():
            lbl.setText("—")
        self._clear_layout(self._flags_layout)
        self._clear_layout(self._apps_layout)
        self._clear_layout(self._sessions_layout)
        self._clear_layout(self._inferred_layout)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _add_flag_row(self, flag: str, count: int) -> None:
        label = _FLAG_LABELS.get(flag, flag.replace("_", " ").title())
        colour = _FLAG_SEVERITY.get(flag, "#6366f1")
        row = QHBoxLayout()
        dot = QLabel("●")
        dot.setStyleSheet(f"color:{colour}; font-size:14px;")
        dot.setFixedWidth(20)
        name_lbl = QLabel(label)
        name_lbl.setStyleSheet("font-size:12px;")
        count_lbl = QLabel(str(count))
        count_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        count_lbl.setStyleSheet(f"color:{colour}; font-weight:600; font-size:12px;")
        row.addWidget(dot)
        row.addWidget(name_lbl)
        row.addStretch()
        row.addWidget(count_lbl)
        container = QWidget()
        container.setLayout(row)
        self._flags_layout.addWidget(container)

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    @staticmethod
    def _format_duration(seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            return f"{int(seconds // 60)}m"
        else:
            return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"
