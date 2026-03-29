"""Debug proxy model behavior."""

from datetime import datetime, timezone
from models.timeline_event import TimelineEvent
from ui.widgets.timeline_model import TimelineTableModel
from ui.timeline_view import _EvidenceFilterProxy
from PyQt6.QtCore import Qt

model = TimelineTableModel()

events = [
    TimelineEvent(
        event_id="e1", sequence_index=0,
        timestamp=datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc),
        iso_timestamp="2024-01-15T14:00:00Z",
        app="com.app1", event_type="APP_OPENED", source="usage_stats",
        evidence_type="DIRECT",
    ),
    TimelineEvent(
        event_id="e2", sequence_index=1,
        timestamp=None, iso_timestamp="UNKNOWN",
        app="com.app2", event_type="APP_INSTALLED", source="packages",
        evidence_type="DIRECT",
    ),
]

model.set_events(events)

print(f"Model rowCount: {model.rowCount()}")
for row in range(model.rowCount()):
    event = model.event_at(row)
    print(f"  Row {row}: {event.app}")

proxy = _EvidenceFilterProxy()
proxy.setSourceModel(model)

print(f"\nProxy rowCount before sort: {proxy.rowCount()}")

# Try sorting
proxy.sort(1, Qt.SortOrder.AscendingOrder)

print(f"Proxy rowCount after sort: {proxy.rowCount()}")

# Check if source model is set
print(f"Proxy sourceModel: {proxy.sourceModel()}")
print(f"Proxy sourceModel rowCount: {proxy.sourceModel().rowCount() if proxy.sourceModel() else 'None'}")

# Try accessing data
for row in range(proxy.rowCount()):
    idx = proxy.createIndex(row, 0)
    print(f"Proxy Row {row}: index valid={idx.isValid()}")
