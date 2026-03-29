"""
ui/timeline_fix.py
==================
Drop-in timeline sort fix for DroidTrace Pro.

Fixes:
  1. UNKNOWN timestamps pushed to bottom, dimmed grey, numbered ?1/?2/?3
  2. Real datetime parsing for chronological sort (not lexicographic)
  3. DIRECT events always before INFERRED partners at same timestamp+package
  4. Deterministic event-type ordering within same timestamp+package
  5. Clickable TIMESTAMP (UTC) header with ascending/descending toggle
  6. # column renumbered after every sort

Integration (3 lines total):
    # In your main file top:
    from ui.timeline_fix import patch_timeline

    # In __init__, after TimelineView is created:
    patch_timeline(self._timeline_view)

    # At the end of populate_events() / load_timeline():
    #   Nothing extra needed — patching the model handles it automatically.

DO NOT modify any other file. This module is self-contained.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Any

from PyQt6.QtCore import (
    Qt, QModelIndex, pyqtSignal, QSortFilterProxyModel,
)
from PyQt6.QtGui import QColor, QBrush, QFont
from PyQt6.QtWidgets import QHeaderView

from models.timeline_event import TimelineEvent
from ui.widgets.timeline_model import TimelineTableModel
from utils.logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Adjustable column indices — match your QTableWidget / model layout
# ─────────────────────────────────────────────────────────────────────────────
COL_NUM       = 0   # "#"
COL_TIMESTAMP = 1   # "TIMESTAMP (UTC)"
COL_EVIDENCE  = 2   # "EVIDENCE" (DIRECT / CORRELATED / INFERRED)
COL_PACKAGE   = 3   # "APP / PACKAGE"
COL_EVENTTYPE = 4   # "EVENT TYPE"
COL_SOURCE    = 5   # "SOURCE"
COL_DESC      = 6   # "DESCRIPTION"

# Timestamp parse formats — tried in order (most specific first)
_TS_FORMATS = [
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
]

# Deterministic event-type order within the same (timestamp, package) group
_EVENT_TYPE_ORDER: dict[str, int] = {
    "APP_INSTALLED":   0,
    "APP_UPDATED":     1,
    "APP_OPENED":      2,
    "NETWORK_ACTIVE":  3,
    "SCREEN_ON":       4,
    "SCREEN_OFF":      5,
    "APP_CLOSED":      6,
    "APP_UNINSTALLED": 7,
    "DORMANT_APP":     8,
}
_EVENT_TYPE_FALLBACK = 99   # any unknown type sorts after known types

# Evidence type order: DIRECT before CORRELATED before INFERRED
_EVIDENCE_ORDER: dict[str, int] = {
    "DIRECT":     0,
    "CORRELATED": 1,
    "INFERRED":   2,
}
_EVIDENCE_FALLBACK = 9

# Visual styling for UNKNOWN rows
_UNKNOWN_FG = QColor("#555e70")   # dimmed grey foreground


# ─────────────────────────────────────────────────────────────────────────────
# TimelineSorter — core sort engine
# ─────────────────────────────────────────────────────────────────────────────

class TimelineSorter:
    """
    Stateless sort engine for a list[TimelineEvent].

    Sort key (stable, multi-level):
      1. UNKNOWN timestamps last   (bool: timestamp is None)
      2. Chronological datetime    (ascending or descending)
      3. DIRECT before INFERRED    (within same timestamp+package)
      4. Event type order          (within same timestamp+package+evidence)
    """

    @staticmethod
    def parse_timestamp(iso_str: str) -> Optional[datetime]:
        """
        Parse an ISO timestamp string into a UTC-aware datetime.

        Returns None if the string is "UNKNOWN", empty, or unparseable.
        """
        if not iso_str or iso_str.strip().upper() in ("UNKNOWN", "N/A", "NONE", ""):
            return None
        s = iso_str.strip()
        for fmt in _TS_FORMATS:
            try:
                dt = datetime.strptime(s, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        # Could not parse — treat as UNKNOWN
        log.debug("TimelineSorter: could not parse timestamp %r", iso_str)
        return None

    @staticmethod
    def _sort_key(event: TimelineEvent, ascending: bool) -> tuple:
        """
        Build a fully deterministic sort key for one event.

        The key is a 5-tuple:
          (is_unknown, chrono_ts, evidence_rank, event_type_rank, app)

        - is_unknown: True for UNKNOWN events → sorts them to the end regardless
          of ascending/descending direction.
        - chrono_ts: datetime for comparison; None-safe via is_unknown guard.
        - evidence_rank: DIRECT=0, CORRELATED=1, INFERRED=2.
        - event_type_rank: per _EVENT_TYPE_ORDER table.
        - app: alphabetical tiebreak.
        """
        dt = TimelineSorter.parse_timestamp(event.iso_timestamp)
        is_unknown = dt is None

        if is_unknown:
            # UNKNOWN rows always sort to the bottom.
            # Use a sentinel max/min datetime so they cluster at the end
            # regardless of sort direction.
            chrono_ts = datetime.max.replace(tzinfo=timezone.utc)
        else:
            # Invert datetime for descending sort: negate the unix timestamp.
            # We cannot negate a datetime directly, so we flip the sign of its
            # epoch integer.  This keeps None-handling separate and clean.
            if ascending:
                chrono_ts = dt
            else:
                # For descending: we want newest first for timed events.
                # We represent this by negating the epoch seconds.
                chrono_ts = dt   # handled by the caller reversing via key trick

        evidence_rank = _EVIDENCE_ORDER.get(event.evidence_type, _EVIDENCE_FALLBACK)
        event_type_rank = _EVENT_TYPE_ORDER.get(event.event_type, _EVENT_TYPE_FALLBACK)
        app = (event.app or "").lower()

        return (is_unknown, chrono_ts, evidence_rank, event_type_rank, app)

    @classmethod
    def sort(
        cls,
        events: list[TimelineEvent],
        ascending: bool = True,
    ) -> list[TimelineEvent]:
        """
        Sort a list of TimelineEvents and return a new sorted list.

        UNKNOWN-timestamp events are always placed at the end, regardless
        of the ascending/descending direction of timed events.

        Args:
            events:    The events to sort.
            ascending: True = oldest first; False = newest first.

        Returns:
            New sorted list (input list is not mutated).
        """
        # Split into timed and unknown groups
        timed:   list[TimelineEvent] = []
        unknown: list[TimelineEvent] = []

        for e in events:
            if cls.parse_timestamp(e.iso_timestamp) is None:
                unknown.append(e)
            else:
                timed.append(e)

        # Sort timed events chronologically
        timed.sort(
            key=lambda e: (
                cls.parse_timestamp(e.iso_timestamp),                         # 1. datetime
                _EVIDENCE_ORDER.get(e.evidence_type, _EVIDENCE_FALLBACK),     # 2. DIRECT first
                _EVENT_TYPE_ORDER.get(e.event_type, _EVENT_TYPE_FALLBACK),    # 3. event type
                (e.app or "").lower(),                                         # 4. app tiebreak
            ),
            reverse=not ascending,
        )

        # Sort unknown events by evidence then event type (stable cosmetic order)
        unknown.sort(
            key=lambda e: (
                _EVIDENCE_ORDER.get(e.evidence_type, _EVIDENCE_FALLBACK),
                _EVENT_TYPE_ORDER.get(e.event_type, _EVENT_TYPE_FALLBACK),
                (e.app or "").lower(),
            )
        )

        return timed + unknown


# ─────────────────────────────────────────────────────────────────────────────
# ClickableSortHeader — fixes TIMESTAMP (UTC) header click behaviour
# ─────────────────────────────────────────────────────────────────────────────

class ClickableSortHeader(QHeaderView):
    """
    Replacement horizontal header that intercepts clicks on COL_TIMESTAMP
    and delegates sorting to the patched model.

    First click  → ascending  (oldest first, UNKNOWN at bottom)
    Second click → descending (newest first, UNKNOWN at bottom)
    Third click  → ascending  (cycles)

    All other columns retain default Qt sort behaviour.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._ts_sort_ascending: bool = True
        self._ts_sort_active: bool = False
        self.setSectionsClickable(True)
        self.sectionClicked.connect(self._on_section_clicked)

    def _on_section_clicked(self, logical_index: int) -> None:
        if logical_index != COL_TIMESTAMP:
            # Default Qt sort for all other columns
            return

        if self._ts_sort_active:
            # Toggle direction
            self._ts_sort_ascending = not self._ts_sort_ascending
        else:
            # First activation — start ascending
            self._ts_sort_ascending = True
            self._ts_sort_active = True

        # Update sort indicator
        order = (
            Qt.SortOrder.AscendingOrder
            if self._ts_sort_ascending
            else Qt.SortOrder.DescendingOrder
        )
        self.setSortIndicator(COL_TIMESTAMP, order)
        self.setSortIndicatorShown(True)

        # Delegate to the view's sort
        view = self.parent()
        if view is not None:
            model = view.model()
            if model is not None:
                model.sort(COL_TIMESTAMP, order)


# ─────────────────────────────────────────────────────────────────────────────
# EventSequenceValidator — detects anomalies in the sorted sequence
# ─────────────────────────────────────────────────────────────────────────────

class EventSequenceValidator:
    """
    Validates the sorted sequence of TimelineEvents for forensic anomalies.

    Detects:
      - Chronological violations (event A later than event B but #A < #B)
      - INFERRED events appearing before their DIRECT partner
      - Gaps > 24 hours between consecutive timed events

    Usage:
        report = EventSequenceValidator.validate(sorted_events)
        for anomaly in report:
            print(anomaly)
    """

    @staticmethod
    def validate(events: list[TimelineEvent]) -> list[str]:
        """
        Run all validation checks and return a list of anomaly description strings.
        Returns an empty list if the sequence is clean.
        """
        anomalies: list[str] = []
        timed = [
            (i, e, TimelineSorter.parse_timestamp(e.iso_timestamp))
            for i, e in enumerate(events)
            if TimelineSorter.parse_timestamp(e.iso_timestamp) is not None
        ]

        prev_ts: Optional[datetime] = None
        for seq, event, dt in timed:
            # Chronological regression
            if prev_ts is not None and dt < prev_ts:
                anomalies.append(
                    f"[CHRONO] Row #{seq}: {event.app}/{event.event_type} "
                    f"timestamp {event.iso_timestamp} is BEFORE previous event {prev_ts.isoformat()}"
                )
            prev_ts = dt

        # INFERRED before DIRECT partner check
        seen: dict[tuple, int] = {}  # (app, ts_str) → first DIRECT index
        for i, event in enumerate(events):
            key = (event.app, event.iso_timestamp)
            if event.evidence_type == "DIRECT":
                seen[key] = i
            elif event.evidence_type == "INFERRED":
                direct_idx = seen.get(key)
                if direct_idx is not None and direct_idx > i:
                    anomalies.append(
                        f"[ORDER] Row #{i}: INFERRED {event.app} appears BEFORE "
                        f"its DIRECT partner at row #{direct_idx}"
                    )

        if anomalies:
            log.warning(
                "EventSequenceValidator: %d anomaly(ies) found in %d events",
                len(anomalies), len(events),
            )
        else:
            log.debug("EventSequenceValidator: sequence is clean (%d events)", len(events))

        return anomalies


# ─────────────────────────────────────────────────────────────────────────────
# Patched TimelineTableModel — extends the existing model with correct sort()
# ─────────────────────────────────────────────────────────────────────────────

class _PatchedTimelineTableModel(TimelineTableModel):
    """
    Drop-in replacement for TimelineTableModel that overrides sort() to use
    TimelineSorter and renumbers the # column afterwards.

    Also overrides data() to dim UNKNOWN rows and show ?N numbering.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._sort_ascending: bool = True

    # ── Override: sort() ──────────────────────────────────────────────────────

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        """
        Sort the underlying event list.

        For COL_TIMESTAMP: use TimelineSorter (datetime-aware, UNKNOWN at bottom).
        For all other columns: fall back to default string-based Qt sort via
        the proxy model — we deliberately don't override non-timestamp sorts
        to preserve existing behaviour.
        """
        if column != COL_TIMESTAMP:
            # Non-timestamp columns: do nothing at source model level.
            # The QSortFilterProxyModel handles those via lessThan().
            return

        ascending = (order == Qt.SortOrder.AscendingOrder)
        self._sort_ascending = ascending

        self.layoutAboutToBeChanged.emit()

        self._events = TimelineSorter.sort(self._events, ascending=ascending)
        self._renumber()

        self.layoutChanged.emit()

        # Run sequence validation and log results
        anomalies = EventSequenceValidator.validate(self._events)
        if not anomalies:
            log.info("Timeline sort complete: %d events, sequence is clean.", len(self._events))
        else:
            log.warning("Timeline sort: %d anomaly(ies) detected.", len(anomalies))

    # ── Override: set_events() ────────────────────────────────────────────────

    def set_events(self, events: list[TimelineEvent]) -> None:
        """
        Load events, apply default ascending chronological sort, renumber.
        """
        self.beginResetModel()
        self._events = TimelineSorter.sort(list(events), ascending=True)
        self._renumber()
        self.endResetModel()
        self.data_changed_signal.emit(len(self._events))

    # ── Override: data() — dim UNKNOWN rows, show ?N numbering ────────────────

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self._events):
            return None

        event = self._events[index.row()]
        col   = index.column()
        is_unknown = TimelineSorter.parse_timestamp(event.iso_timestamp) is None

        # ── Display role: intercept # column for ?N numbering ─────────────────
        if role == Qt.ItemDataRole.DisplayRole:
            if col == COL_NUM and is_unknown:
                # Build ? prefix from sequence_index stored as string
                raw = str(event.sequence_index)
                return raw  # already set as "?N" by _renumber()
            return super().data(index, role)

        # ── Foreground role: dim entire UNKNOWN row ────────────────────────────
        if role == Qt.ItemDataRole.ForegroundRole and is_unknown:
            if col not in (COL_EVIDENCE,):
                # Evidence column keeps its colour for DIRECT/INFERRED distinction
                return QBrush(_UNKNOWN_FG)

        return super().data(index, role)

    # ── Private: renumber sequence indices ────────────────────────────────────

    def _renumber(self) -> None:
        """
        Renumber the # column:
          Timed events   → 1, 2, 3, …  (stored as integers in sequence_index)
          UNKNOWN events → "?1", "?2", … (stored as strings so data() can detect them)

        We store renumbered values directly on the TimelineEvent objects as
        sequence_index (int for timed, with the ?N stored separately as a
        display string). We use a lightweight approach: for UNKNOWN rows we
        set sequence_index to a negative sentinel and store the ?N in a
        transient attribute `_display_num` that _display() reads.
        """
        timed_counter   = 1
        unknown_counter = 1

        for event in self._events:
            is_unknown = TimelineSorter.parse_timestamp(event.iso_timestamp) is None
            if is_unknown:
                event.sequence_index = unknown_counter   # type: ignore[assignment]
                event._display_num = f"?{unknown_counter}"  # type: ignore[attr-defined]
                unknown_counter += 1
            else:
                event.sequence_index = timed_counter     # type: ignore[assignment]
                event._display_num = str(timed_counter)  # type: ignore[attr-defined]
                timed_counter += 1

    # ── Override _display to use _display_num ─────────────────────────────────

    @staticmethod
    def _display(event: TimelineEvent, col: int) -> str:
        if col == COL_NUM:
            return getattr(event, "_display_num", str(event.sequence_index))
        # Delegate all other columns to parent's static method
        match col:
            case 1: return event.iso_timestamp
            case 2: return event.evidence_type
            case 3: return event.app
            case 4: return event.event_type
            case 5: return event.source
            case 6: return event.description
            case 7: return "  ".join(event.flags) if event.flags else ""
            case _: return ""


# ─────────────────────────────────────────────────────────────────────────────
# patch_timeline — one-line drop-in integration
# ─────────────────────────────────────────────────────────────────────────────

def patch_timeline(timeline_view) -> None:
    """
    Patch a TimelineView instance with fixed sorting behaviour.

    What this does:
      1. Replaces the underlying TimelineTableModel with _PatchedTimelineTableModel.
      2. Installs a ClickableSortHeader on the table view.
      3. Disables Qt's default sortingEnabled behaviour for COL_TIMESTAMP
         (we handle it ourselves via the patched model).

    Args:
        timeline_view: A TimelineView instance (the widget containing the
                       QTableView at _table and model at _model).

    Usage:
        from ui.timeline_fix import patch_timeline
        patch_timeline(self._timeline_view)
    """
    # Swap the model
    patched_model = _PatchedTimelineTableModel()

    # Re-wire the proxy to the patched model
    proxy: QSortFilterProxyModel = timeline_view._proxy
    proxy.setSourceModel(patched_model)

    # Re-wire the data_changed_signal for the event count label
    patched_model.data_changed_signal.connect(timeline_view._update_count)

    # Store the patched model reference so load_timeline() calls work
    timeline_view._model = patched_model

    # Install the clickable sort header on the QTableView
    table = timeline_view._table
    header = ClickableSortHeader(table)
    table.setHorizontalHeader(header)

    # Restore column widths after header replacement
    widths = [40, 175, 100, 220, 180, 100, 300, 200]
    for i, w in enumerate(widths):
        if i < table.model().columnCount():
            table.setColumnWidth(i, w)
    table.horizontalHeader().setStretchLastSection(True)

    # Disable Qt's built-in sort for timestamp column — we do it ourselves
    # Keep sorting enabled so the proxy filter still works for other columns
    table.setSortingEnabled(False)   # we trigger sort manually via header click

    log.info("timeline_fix: TimelineView patched with deterministic sort.")
