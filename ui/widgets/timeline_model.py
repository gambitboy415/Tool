"""
ui/widgets/timeline_model.py
==============================
QAbstractTableModel binding a list[TimelineEvent] to the Qt timeline view.

Responsibilities:
  - Expose timeline data to QTableView with correct column count and roles
  - Provide display text, alignment, font, and background colour per cell
  - Support filtering by evidence_type, flag presence, and date range
  - Emit a custom signal when the underlying data is replaced (full refresh)

Column layout (9 columns):
  0  #            sequence_index
  1  Timestamp    iso_timestamp
  2  Evidence     evidence_type (DIRECT / CORRELATED / INFERRED)
  3  App          app (package name)
  4  Event Type   event_type
  5  Source       source
  6  Description  description
  7  Flags        flags (comma-separated)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from PyQt6.QtCore import (
    QAbstractTableModel, QModelIndex, Qt, pyqtSignal, QSortFilterProxyModel
)
from PyQt6.QtGui import QColor, QFont, QBrush

from models.timeline_event import TimelineEvent

# ── Column metadata ───────────────────────────────────────────────────────────
_COLUMNS = ["#", "Timestamp (UTC)", "Evidence", "App / Package",
            "Event Type", "Source", "Description", "Flags"]

# Evidence type → hex colour for the badge cell background
_EVIDENCE_COLOURS = {
    "DIRECT":     QColor("#1a3a2a"),   # dark green tint
    "CORRELATED": QColor("#1a2a45"),   # dark blue tint
    "INFERRED":   QColor("#2e1a4a"),   # dark purple tint
}
_EVIDENCE_TEXT = {
    "DIRECT":     QColor("#22c55e"),
    "CORRELATED": QColor("#3b82f6"),
    "INFERRED":   QColor("#a855f7"),
}
_FLAG_BG    = QColor("#2a1800")    # flagged row background
_FLAG_FG    = QColor("#f97316")    # flag text colour
_MUTED      = QColor("#8892a4")
_MONO_FONT  = QFont("Cascadia Code", 9)
_COL_EVIDENCE = 2


class TimelineTableModel(QAbstractTableModel):
    """
    Qt table model for a :class:`TimelineEvent` list.

    Usage:
        model = TimelineTableModel()
        model.set_events(timeline)
        table_view.setModel(model)
    """

    data_changed_signal = pyqtSignal(int)  # emits total event count

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._events: list[TimelineEvent] = []

    def set_events(self, events: list[TimelineEvent]) -> None:
        """Replace the full event list and refresh the view."""
        self.beginResetModel()
        self._events = list(events)
        self.endResetModel()
        self.data_changed_signal.emit(len(self._events))

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._events)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(_COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return _COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self._events):
            return None

        event = self._events[index.row()]
        col   = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            return self._display(event, col)

        if role == Qt.ItemDataRole.BackgroundRole:
            if event.flags:
                return QBrush(_FLAG_BG)
            if col == _COL_EVIDENCE:
                return QBrush(_EVIDENCE_COLOURS.get(event.evidence_type, QColor("#1a1d27")))
            return None

        if role == Qt.ItemDataRole.ForegroundRole:
            if col == _COL_EVIDENCE:
                return QBrush(_EVIDENCE_TEXT.get(event.evidence_type, QColor("#e2e8f0")))
            if col == 7 and event.flags:   # flags column
                return QBrush(_FLAG_FG)
            if col in (1, 0):              # timestamp and index — muted
                return QBrush(_MUTED)
            return None

        if role == Qt.ItemDataRole.FontRole:
            if col in (0, 1, 3):          # mono columns
                return _MONO_FONT
            return None

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col == 0:
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(event)

        # Custom role: expose the raw TimelineEvent
        if role == Qt.ItemDataRole.UserRole:
            return event

        return None

    # ── Display text per column ───────────────────────────────────────────────

    @staticmethod
    def _display(event: TimelineEvent, col: int) -> str:
        match col:
            case 0: return str(event.sequence_index)
            case 1: return event.iso_timestamp
            case 2: return event.evidence_type
            case 3: return event.app
            case 4: return event.event_type
            case 5: return event.source
            case 6: return event.description
            case 7: return "  ".join(event.flags) if event.flags else ""
            case _: return ""

    @staticmethod
    def _tooltip(event: TimelineEvent) -> str:
        lines = [
            f"Event ID: {event.event_id}",
            f"Sequence: #{event.sequence_index}",
            f"Timestamp: {event.iso_timestamp}",
            f"App: {event.app}",
            f"Type: {event.event_type}",
            f"Source: {event.source}",
            f"Evidence: {event.evidence_type}",
        ]
        if event.flags:
            lines.append(f"Flags: {', '.join(event.flags)}")
        if event.reason:
            lines.append(f"Reasoning: {event.reason}")
        if event.correlation_id:
            lines.append(f"Correlation ID: {event.correlation_id}")
        if event.normalization_flags:
            lines.append(f"Norm flags: {', '.join(event.normalization_flags)}")
        return "\n".join(lines)

    def event_at(self, row: int) -> Optional[TimelineEvent]:
        """Retrieve the TimelineEvent at a given row index."""
        if 0 <= row < len(self._events):
            return self._events[row]
        return None
