"""
ui/main_window.py
==================
Application shell for DroidTrace Pro.

Orchestrates all panels and the full extraction → analysis pipeline.

Layout:
  ┌─────────────────────────────────────────────────────────────────┐
  │  Menu Bar  │  Tool Bar                                          │
  ├──────────┬─────────────────────────────────────┬───────────────┤
  │  Device  │                                     │   Analysis    │
  │  Panel   │        Timeline View                │   Panel       │
  │          │        (centre)                     │   (right)     │
  │  Artifact│                                     │               │
  │  Panel   │                                     │               │
  ├──────────┴─────────────────────────────────────┴───────────────┤
  │  Status Bar                                                     │
  └─────────────────────────────────────────────────────────────────┘

Background pipeline (QThread):
  ExtractionWorker
    ├── DataCollector.collect_all()
    ├── parse_artifacts()
    ├── normalize_events()
    ├── build_timeline()
    ├── CorrelationEngine.run()
    └── InferenceEngine.run()
  → emits pipeline_complete(timeline, inf_report, corr_report)
  → main thread updates all panels
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QFont, QIcon
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QSplitter, QStatusBar, QLabel, QMenuBar, QToolBar,
    QMessageBox, QApplication,
)

from core.adb.adb_connector import AdbConnector
from core.collectors.data_collector import DataCollector
from core.parsers.parser import parse_artifacts
from core.timeline.normalizer import normalize_events, NormalizationConfig
from core.timeline.timeline_builder import build_timeline
from core.timeline.validator import TimelineValidator
from core.timeline.session_engine import build_sessions
from core.correlation.correlation_engine import CorrelationEngine, CorrelationReport
from core.inference.inference_engine import InferenceEngine, InferenceReport
from core.analysis.behavioral_summary import get_behavioral_summary, BehavioralSummary
from core.reporting.report_generator import ReportGenerator

from models.device_info import DeviceInfo
from models.timeline_event import TimelineEvent

from ui.device_panel import DevicePanel
from ui.artifact_panel import ArtifactPanel
from ui.timeline_view import TimelineView
from ui.analysis_panel import AnalysisPanel
from ui.progress_dialog import ProgressDialog
from ui.report_dialog import ReportDialog

from utils.logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Background pipeline worker
# ─────────────────────────────────────────────────────────────────────────────

class ExtractionWorker(QThread):
    """
    Runs the full 6-stage analysis pipeline in a background thread.

    Stages:              Progress %
      1. Collect              0–25
      2. Parse               25–35
      3. Normalize           35–45
      4. Build Timeline      45–55
      5. Session Recon       55–65
      6. Validation          65–75
      7. Correlation         75–85
      8. Inference           85–100

    Signals:
        progress(int, str)          — (percent, stage_description)
        log_message(str)            — status text for the progress dialog
        pipeline_complete(list, InferenceReport, CorrelationReport)
        pipeline_error(str)
    """

    progress          = pyqtSignal(int, str)
    log_message       = pyqtSignal(str)
    pipeline_complete = pyqtSignal(list, object, object, object)   # timeline, inf, corr, behavioral
    pipeline_error    = pyqtSignal(str)

    def __init__(
        self,
        connector: AdbConnector,
        options: dict,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._connector = connector
        self._options   = options

    def run(self) -> None:
        try:
            collected_at = datetime.now(tz=timezone.utc)

            # ── Stage 1: Collect ─────────────────────────────────────────────
            self.progress.emit(5, "📡  Collecting artifacts from device…")
            self.log_message.emit("Starting data collection…")

            collector = DataCollector(
                connector=self._connector,
            )

            def _collect_progress(done: int, total: int) -> None:
                pct = 5 + int(done / total * 25) if total else 5
                self.progress.emit(pct, f"📡  Collecting… ({done}/{total} sources)")

            coll_result = collector.collect_all(
                options=self._options,
                progress_callback=_collect_progress,
            )
            self.log_message.emit(coll_result.summary())
            self.progress.emit(25, "✅  Collection complete")

            # ── Stage 2: Parse ───────────────────────────────────────────────
            self.progress.emit(27, "🔍  Parsing artifact data…")
            self.log_message.emit("Parsing raw artifacts…")

            parsed = parse_artifacts(
                coll_result.artifacts,
                dedup=self._options.get("dedup", True),
            )
            self.log_message.emit(f"Parsed {len(parsed)} raw events")
            self.progress.emit(35, "✅  Parsing complete")

            # ── Stage 3: Normalize ───────────────────────────────────────────
            self.progress.emit(37, "⚙  Normalising timestamps and filtering noise…")
            self.log_message.emit("Running normalisation pipeline…")

            norm_config = NormalizationConfig(
                remove_noise_packages=self._options.get("remove_noise", True),
                remove_noise_event_types=self._options.get("remove_noise", True),
            )
            normalised, norm_report = normalize_events(
                parsed, collection_time=collected_at, config=norm_config
            )
            self.log_message.emit(norm_report.summary())
            self.progress.emit(45, "✅  Normalisation complete")

            # ── Stage 4: Build Timeline ───────────────────────────────────────
            self.progress.emit(47, "📅  Reconstructing timeline…")
            self.log_message.emit("Building unified timeline…")

            timeline = build_timeline(normalised)
            self.log_message.emit(f"Timeline: {len(timeline)} events")
            self.progress.emit(55, "✅  Timeline built")

            # ── Stage 5: Session Reconstruction ────────────────────────────────
            self.progress.emit(57, "🔄  Synthesizing user activity sessions…")
            self.log_message.emit("Synthesizing user activity sessions…")

            timeline = build_sessions(
                timeline, 
                collection_time=collected_at,
                summarize=self._options.get("summarize", False)
            )
            self.log_message.emit(f"Sessions synthesized: {len([e for e in timeline if e.event_type == 'APP_SESSION'])}")
            self.progress.emit(65, "✅  Sessions complete")

            # ── Stage 6: Validation ───────────────────────────────────────────
            self.progress.emit(67, "🛡️  Validating forensic integrity…")
            self.log_message.emit("Running forensic validation suite…")

            validator = TimelineValidator()
            timeline = validator.validate_and_repair(timeline)
            self.log_message.emit(f"Validation: {len(timeline)} events remain")
            self.progress.emit(75, "✅  Validation complete")

            # ── Stage 6: Correlation ──────────────────────────────────────────
            self.progress.emit(72, "🔗  Running correlation engine…")
            self.log_message.emit("Applying correlation rules…")

            corr_engine  = CorrelationEngine()
            corr_report  = corr_engine.run(timeline)
            self.log_message.emit(corr_report.summary())
            self.progress.emit(85, "✅  Correlation complete")

            # ── Stage 7: Inference ────────────────────────────────────────────
            self.progress.emit(87, "🧠  Running inference engine…")
            self.log_message.emit("Applying behavioral pattern rules…")

            inf_engine = InferenceEngine()
            inf_report = inf_engine.run(timeline)
            self.log_message.emit(inf_report.summary())
            
            # ── Final Analysis: Behavioral Summary ────────────────────────────
            behavioral_summary = get_behavioral_summary(timeline)
            self.log_message.emit(f"Forensic logic complete. Sessions: {behavioral_summary.session_count}")
            
            self.progress.emit(100, "✅  Analysis complete")

            self.pipeline_complete.emit(timeline, inf_report, corr_report, behavioral_summary)

        except Exception as exc:  # noqa: BLE001
            log.error("ExtractionWorker error: %s", exc, exc_info=True)
            self.pipeline_error.emit(str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# MainWindow
# ─────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    """
    Primary application window for DroidTrace Pro.

    Connects all panels and manages the extraction worker lifecycle.
    """

    def __init__(self) -> None:
        super().__init__()
        self._timeline:     list[TimelineEvent]         = []
        self._device_info:  Optional[DeviceInfo]        = None
        self._worker:       Optional[ExtractionWorker]  = None
        self._prog_dialog:  Optional[ProgressDialog]    = None
        
        # Latest analysis results for export
        self._inf_report:   Optional[InferenceReport]   = None
        self._corr_report:  Optional[CorrelationReport]  = None
        self._behavior_sum: Optional[BehavioralSummary]  = None

        self._setup_window()
        self._build_ui()
        self._apply_stylesheet()
        self._connect_signals()

        log.info("MainWindow initialised")

    # ── Window setup ──────────────────────────────────────────────────────────

    def _setup_window(self) -> None:
        self.setWindowTitle("DroidTrace Pro — Android Forensic Analyser")
        self.setMinimumSize(1280, 780)
        self.resize(1600, 900)

    def _build_ui(self) -> None:
        # ── Menu bar ─────────────────────────────────────────────────────────
        menu = self.menuBar()
        file_menu = menu.addMenu("File")
        self._export_action = QAction("Export Report…", self)
        self._export_action.setShortcut("Ctrl+E")
        self._export_action.setEnabled(False)
        file_menu.addAction(self._export_action)
        file_menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(QApplication.quit)
        file_menu.addAction(quit_action)

        help_menu = menu.addMenu("Help")
        about_action = QAction("About DroidTrace Pro", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

        # ── Tool bar ─────────────────────────────────────────────────────────
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setObjectName("mainToolbar")
        self.addToolBar(toolbar)

        self._tb_title = QLabel("  🔬 DroidTrace Pro  ")
        self._tb_title.setStyleSheet("font-size:14px; font-weight:700; color:#6366f1;")
        toolbar.addWidget(self._tb_title)

        toolbar.addSeparator()
        self._tb_status = QLabel("  Ready — connect a device to begin")
        self._tb_status.setStyleSheet("color:#8892a4; font-size:12px;")
        toolbar.addWidget(self._tb_status)

        # ── Central layout ────────────────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(0)

        # ── Left sidebar ──────────────────────────────────────────────────────
        left_panel = QWidget()
        left_panel.setFixedWidth(260)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(12)

        self._device_panel   = DevicePanel()
        self._artifact_panel = ArtifactPanel()
        left_layout.addWidget(self._device_panel, stretch=1)
        left_layout.addWidget(self._artifact_panel, stretch=1)

        # ── Main splitter ─────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._timeline_view   = TimelineView()
        self._analysis_panel  = AnalysisPanel()
        self._analysis_panel.setFixedWidth(310)

        splitter.addWidget(self._timeline_view)
        splitter.addWidget(self._analysis_panel)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        root_layout.addWidget(left_panel)
        root_layout.addWidget(splitter, stretch=1)

        # ── Status bar ───────────────────────────────────────────────────────
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready")

        self._event_count_label = QLabel("0 events")
        self._event_count_label.setStyleSheet("color:#8892a4; font-size:11px; padding-right:12px;")
        self._status_bar.addPermanentWidget(self._event_count_label)

    def _connect_signals(self) -> None:
        self._device_panel.device_connected.connect(self._on_device_connected)
        self._device_panel.device_disconnected.connect(self._on_device_disconnected)
        self._device_panel.status_message.connect(self._status_bar.showMessage)
        self._artifact_panel.extraction_requested.connect(self._on_extract_requested)
        self._timeline_view.event_selected.connect(self._on_event_selected)
        self._analysis_panel.app_filter_requested.connect(self._on_app_filter)
        self._export_action.triggered.connect(self._on_export)

    # ── Slots ──────────────────────────────────────────────────────────────────

    @pyqtSlot(object)
    def _on_device_connected(self, info: DeviceInfo) -> None:
        self._device_info = info
        self._artifact_panel.setEnabled(True)
        self._tb_status.setText(f"  📱  {info.display_name()}  |  {info.serial}")
        self._status_bar.showMessage(f"Connected: {info.display_name()}")

    @pyqtSlot()
    def _on_device_disconnected(self) -> None:
        self._device_info = None
        self._artifact_panel.setEnabled(False)
        self._tb_status.setText("  Ready — connect a device to begin")
        self._export_action.setEnabled(False)

    @pyqtSlot(dict)
    def _on_extract_requested(self, options: dict) -> None:
        connector = self._device_panel.connector
        if connector is None:
            QMessageBox.warning(self, "No Device", "Please connect a device first.")
            return

        # Set up progress dialog
        self._prog_dialog = ProgressDialog(self, title="Extracting & Analysing…")
        self._artifact_panel.set_extracting(True)

        # Create and connect worker
        self._worker = ExtractionWorker(connector=connector, options=options)
        self._worker.progress.connect(self._prog_dialog.set_progress)
        self._worker.log_message.connect(self._prog_dialog.append_log)
        self._worker.pipeline_complete.connect(self._on_pipeline_complete)
        self._worker.pipeline_error.connect(self._on_pipeline_error)
        self._worker.finished.connect(lambda: self._artifact_panel.set_extracting(False))

        self._worker.start()
        self._prog_dialog.exec()

    @pyqtSlot(list, object, object, object)
    def _on_pipeline_complete(
        self,
        timeline: list,
        inf_report: InferenceReport,
        corr_report: CorrelationReport,
        behavioral_summary: BehavioralSummary,
    ) -> None:
        self._timeline     = timeline
        self._inf_report   = inf_report
        self._corr_report  = corr_report
        self._behavior_sum = behavioral_summary

        # Update all panels
        self._timeline_view.load_timeline(timeline)
        self._analysis_panel.update_analysis(
            timeline,
            inference_report=inf_report,
            correlation_report=corr_report,
            behavioral_summary=behavioral_summary,
        )
        self._event_count_label.setText(f"{len(timeline):,} events")
        self._export_action.setEnabled(True)
        self._status_bar.showMessage(
            f"Analysis complete — {len(timeline)} events | "
            f"{inf_report.flagged_events} flagged | "
            f"{inf_report.inferred_added} detections"
        )

        if self._prog_dialog:
            self._prog_dialog.mark_complete()

        log.info("Pipeline complete: %d events loaded into UI", len(timeline))

    @pyqtSlot(str)
    def _on_pipeline_error(self, error: str) -> None:
        if self._prog_dialog:
            self._prog_dialog.mark_error(error)
        self._status_bar.showMessage(f"Error: {error[:100]}")
        QMessageBox.critical(self, "Extraction Error", error)

    @pyqtSlot(object)
    def _on_event_selected(self, event: TimelineEvent) -> None:
        self._status_bar.showMessage(
            f"#{event.sequence_index}  {event.iso_timestamp}  |  "
            f"{event.event_type}  |  {event.app}"
        )

    @pyqtSlot(str)
    def _on_app_filter(self, app: str) -> None:
        self._timeline_view._search_box.setText(app)
        self._status_bar.showMessage(f"Filtering timeline: {app}")

    @pyqtSlot()
    def _on_export(self) -> None:
        if not self._timeline or not self._device_info:
            QMessageBox.information(self, "No Data", "Run an extraction before exporting.")
            return

        dlg = ReportDialog(self)
        dlg.export_requested.connect(self._do_export)
        dlg.exec()

    def _do_export(self, fmt: str, out_dir: str, only_suspicious: bool = False, include_behavioral: bool = True) -> None:
        try:
            generator = ReportGenerator(
                device=self._device_info,
                collection_time=datetime.now(tz=timezone.utc),
                output_dir=Path(out_dir),
            )
            
            events_to_export = self._timeline
            if only_suspicious:
                events_to_export = [
                    e for e in self._timeline
                    if e.severity in ("IMPORTANT", "SUSPICIOUS") or e.flags
                ]
                
            stats = {}
            if self._inf_report:
                stats["inference"] = self._inf_report.__dict__
            if self._corr_report:
                stats["correlation"] = self._corr_report.__dict__

            report_path = generator.generate(
                events_to_export, 
                fmt=fmt,
                stats=stats,
                include_behavioral=include_behavioral
            )
            self._status_bar.showMessage(f"Report saved: {report_path}")
            QMessageBox.information(
                self, "Export Complete",
                f"Report saved to:\n{report_path}"
            )
            # Auto-open
            if fmt == "html":
                if sys.platform == "win32":
                    os.startfile(str(report_path))
                else:
                    import subprocess
                    opener = "open" if sys.platform == "darwin" else "xdg-open"
                    try:
                        subprocess.Popen([opener, str(report_path)])
                    except Exception:
                        pass
        except Exception as exc:
            log.error("Report export failed: %s", exc, exc_info=True)
            QMessageBox.critical(self, "Export Error", str(exc))

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About DroidTrace Pro",
            "<h2>DroidTrace Pro v1.0.0</h2>"
            "<p>Android Forensic Timeline Analyser</p>"
            "<p>Extracts and correlates Android device artifacts via ADB.<br>"
            "Deterministic, rule-based analysis. No ML. No root required.</p>"
            "<p><b>Architecture:</b> PyQt6 · ADB · Rule-based inference</p>"
        )

    # ── Stylesheet ─────────────────────────────────────────────────────────────

    def _apply_stylesheet(self) -> None:
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #0f1117;
                color: #e2e8f0;
                font-family: 'Segoe UI', system-ui, sans-serif;
                font-size: 13px;
            }
            QMenuBar {
                background: #1a1d27;
                color: #e2e8f0;
                border-bottom: 1px solid #2e344a;
                padding: 2px 8px;
            }
            QMenuBar::item:selected { background: #2e344a; border-radius: 4px; }
            QMenu {
                background: #1a1d27;
                border: 1px solid #2e344a;
                border-radius: 6px;
            }
            QMenu::item:selected { background: #6366f1; border-radius: 3px; }
            QToolBar {
                background: #13151f;
                border-bottom: 1px solid #2e344a;
                padding: 4px;
                spacing: 6px;
            }
            QGroupBox {
                font-size: 11px;
                font-weight: 600;
                color: #8892a4;
                border: 1px solid #2e344a;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 6px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 6px;
                left: 10px;
            }
            QPushButton {
                background: #22263a;
                color: #e2e8f0;
                border: 1px solid #2e344a;
                border-radius: 5px;
                padding: 5px 14px;
            }
            QPushButton:hover { background: #2e344a; }
            QPushButton:pressed { background: #1a1d27; }
            QPushButton:disabled { color: #3d4461; border-color: #1e2235; }
            QPushButton#primaryBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4f46e5, stop:1 #6366f1);
                color: #ffffff;
                border: none;
                font-weight: 600;
            }
            QPushButton#primaryBtn:hover { background: #6366f1; }
            QPushButton#dangerBtn { background: #2a1418; color: #ef4444; border-color: #3d1a1a; }
            QPushButton#dangerBtn:hover { background: #3d1a1a; }
            QPushButton#smallBtn { font-size: 11px; padding: 3px 10px; }
            QPushButton:checkable:checked {
                background: #1e254a;
                border-color: #6366f1;
                color: #818cf8;
            }
            QComboBox {
                background: #1a1d27;
                border: 1px solid #2e344a;
                border-radius: 5px;
                padding: 4px 10px;
                color: #e2e8f0;
            }
            QComboBox::drop-down { border: none; width: 20px; }
            QLineEdit {
                background: #1a1d27;
                border: 1px solid #2e344a;
                border-radius: 5px;
                padding: 4px 10px;
                color: #e2e8f0;
            }
            QLineEdit:focus { border-color: #6366f1; }
            QTableView {
                background: #0f1117;
                alternate-background-color: #13151f;
                border: 1px solid #2e344a;
                border-radius: 6px;
                gridline-color: transparent;
                selection-background-color: #1e254a;
            }
            QHeaderView::section {
                background: #1a1d27;
                color: #8892a4;
                border: none;
                border-bottom: 1px solid #2e344a;
                padding: 6px 10px;
                font-size: 11px;
                font-weight: 600;
                text-transform: uppercase;
            }
            QScrollBar:vertical {
                background: #13151f;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #2e344a;
                border-radius: 4px;
                min-height: 24px;
            }
            QScrollBar::handle:vertical:hover { background: #4f4f7a; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar:horizontal {
                background: #13151f;
                height: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:horizontal { background: #2e344a; border-radius: 4px; }
            QStatusBar {
                background: #13151f;
                border-top: 1px solid #2e344a;
                color: #8892a4;
                font-size: 11px;
            }
            QProgressBar {
                background: #1a1d27;
                border: 1px solid #2e344a;
                border-radius: 4px;
                text-align: center;
                color: #e2e8f0;
                font-size: 11px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4f46e5, stop:1 #6366f1);
                border-radius: 3px;
            }
            QCheckBox { spacing: 6px; }
            QCheckBox::indicator {
                width: 14px; height: 14px;
                border: 1px solid #2e344a;
                border-radius: 3px;
                background: #1a1d27;
            }
            QCheckBox::indicator:checked {
                background: #6366f1;
                border-color: #6366f1;
            }
            QSplitter::handle { background: #2e344a; width: 1px; }
            QDialog { background: #1a1d27; }
            QRadioButton { spacing: 6px; }
            QRadioButton::indicator {
                width: 14px; height: 14px;
                border: 1px solid #2e344a;
                border-radius: 7px;
                background: #0f1117;
            }
            QRadioButton::indicator:checked { background: #6366f1; border-color: #6366f1; }
        """)
