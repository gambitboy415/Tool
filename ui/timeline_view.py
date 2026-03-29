"""
ui/timeline_view.py
=====================
Filterable, sortable timeline table view widget.

Features:
  - Full-height QTableView bound to TimelineTableModel
  - Live filter bar: text search across all columns
  - Evidence type filter buttons (ALL / DIRECT / CORRELATED / INFERRED)
  - Flagged-only toggle button
  - Row click → event detail sidebar pop-out
  - Export selected rows to CSV
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QSortFilterProxyModel, pyqtSlot, pyqtSignal, QRegularExpression
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableView, QLineEdit,
    QPushButton, QLabel, QHeaderView, QAbstractItemView,
    QSizePolicy, QFrame,
)

from ui.widgets.timeline_model import TimelineTableModel
from models.timeline_event import TimelineEvent
from utils.logger import get_logger

log = get_logger(__name__)


class _EvidenceFilterProxy(QSortFilterProxyModel):
    """
    Proxy model that filters by:
      - Full-text search string (any column)
      - Evidence type ("DIRECT", "CORRELATED", "INFERRED", or "" for all)
      - Flagged-only toggle

    Custom sorting:
      - For timestamp column: handles UNKNOWN timestamps by sorting them by sequence_index

    Execution order:
      - Filtering is applied FIRST (via filterAcceptsRow)
      - Then sorting is applied (via lessThan)
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._evidence_filter = ""
        self._flagged_only = False
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setFilterKeyColumn(-1)   # search all columns

    def set_evidence_filter(self, value: str) -> None:
        self._evidence_filter = value
        self.invalidateFilter()

    def set_flagged_only(self, value: bool) -> None:
        self._flagged_only = value
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        source_model = self.sourceModel()
        if not isinstance(source_model, TimelineTableModel):
            return True

        event = source_model.event_at(source_row)
        if event is None:
            return True

        # Evidence type filter
        if self._evidence_filter and event.evidence_type != self._evidence_filter:
            return False

        # Flagged-only filter
        if self._flagged_only and not event.flags:
            return False

        # Text search — check all column display values
        pattern = self.filterRegularExpression().pattern()
        if pattern:
            searchable = " ".join([
                event.iso_timestamp, event.app, event.event_type,
                event.source, event.description, " ".join(event.flags),
                event.evidence_type,
            ])
            if not QRegularExpression(pattern, QRegularExpression.PatternOption.CaseInsensitiveOption).match(searchable).hasMatch():
                return False

        return True

    def lessThan(self, source_left, source_right) -> bool:
        """
        Custom sort comparator.
        For timestamp column: use custom sort role that handles UNKNOWN timestamps correctly.
        """
        # Apply custom sorting to sequence index (col 0) and timestamp (col 1)
        sort_col = self.sortColumn()
        if sort_col in (0, 1):
            source_model = self.sourceModel()
            if isinstance(source_model, TimelineTableModel):
                # Use custom sort role (UserRole + 1)
                left_val = source_model.data(source_left, Qt.ItemDataRole.UserRole + 1)
                right_val = source_model.data(source_right, Qt.ItemDataRole.UserRole + 1)
                if left_val is not None and right_val is not None:
                    return left_val < right_val

        # Default behavior for other columns
        return super().lessThan(source_left, source_right)


class TimelineView(QWidget):
    """
    Main timeline display widget.

    Signals:
        event_selected(TimelineEvent): Emitted when a row is clicked.
    """

    event_selected = pyqtSignal(object)   # TimelineEvent

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._model = TimelineTableModel()
        self._proxy = _EvidenceFilterProxy()
        self._proxy.setSourceModel(self._model)
        self._build_ui()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # ── Filter bar ──────────────────────────────────────────────────────
        filter_row = QHBoxLayout()

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("🔍  Search events…")
        self._search_box.setFixedHeight(34)
        self._search_box.textChanged.connect(self._on_search)
        filter_row.addWidget(self._search_box, stretch=3)

        # Evidence type buttons
        self._filter_btns: dict[str, QPushButton] = {}
        for label in ("All", "Direct", "Correlated", "Inferred"):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedHeight(32)
            btn.setFixedWidth(90)
            btn.clicked.connect(lambda checked, l=label: self._on_evidence_filter(l))
            filter_row.addWidget(btn)
            self._filter_btns[label] = btn
        self._filter_btns["All"].setChecked(True)

        # Flagged-only toggle
        self._flagged_btn = QPushButton("⚑  Flagged Only")
        self._flagged_btn.setCheckable(True)
        self._flagged_btn.setFixedHeight(32)
        self._flagged_btn.toggled.connect(self._on_flagged_toggle)
        filter_row.addWidget(self._flagged_btn)

        layout.addLayout(filter_row)

        # ── Event count label ────────────────────────────────────────────────
        self._count_label = QLabel("0 events")
        self._count_label.setStyleSheet("color: #8892a4; font-size: 11px;")
        layout.addWidget(self._count_label)

        # ── Table view ───────────────────────────────────────────────────────
        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)

        # Column widths
        widths = [40, 175, 100, 220, 180, 100, 300, 200]
        for i, w in enumerate(widths):
            self._table.setColumnWidth(i, w)

        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._table.clicked.connect(self._on_row_clicked)
        layout.addWidget(self._table)

        # Default sort: Chronological Descending (Newest First)
        self._table.sortByColumn(1, Qt.SortOrder.DescendingOrder)

        # Receive count updates from model
        self._model.data_changed_signal.connect(self._update_count)

    # ── Slots ──────────────────────────────────────────────────────────────────

    @pyqtSlot(str)
    def _on_search(self, text: str) -> None:
        self._proxy.setFilterRegularExpression(text)
        self._update_count(self._model.rowCount())

    def _on_evidence_filter(self, label: str) -> None:
        for name, btn in self._filter_btns.items():
            btn.setChecked(name == label)
        value = "" if label == "All" else label.upper()
        self._proxy.set_evidence_filter(value)
        self._update_count(self._model.rowCount())

    @pyqtSlot(bool)
    def _on_flagged_toggle(self, checked: bool) -> None:
        self._proxy.set_flagged_only(checked)
        self._update_count(self._model.rowCount())

    @pyqtSlot()
    def _on_row_clicked(self) -> None:
        indexes = self._table.selectedIndexes()
        if not indexes:
            return
        source_index = self._proxy.mapToSource(indexes[0])
        event = self._model.event_at(source_index.row())
        if event:
            self.event_selected.emit(event)

    @pyqtSlot(int)
    def _update_count(self, _total: int) -> None:
        visible = self._proxy.rowCount()
        total   = self._model.rowCount()
        if visible == total:
            self._count_label.setText(f"{total:,} events")
        else:
            self._count_label.setText(f"{visible:,} of {total:,} events")

    # ── Public API ─────────────────────────────────────────────────────────────

    def load_timeline(self, events: list[TimelineEvent]) -> None:
        """Load a new timeline into the view."""
        self._model.set_events(events)
        self._table.resizeRowsToContents()
        log.info("TimelineView: loaded %d events", len(events))
