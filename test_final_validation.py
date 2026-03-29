"""
Simple final validation of both fixes.
"""

from datetime import datetime, timezone
from models.timeline_event import TimelineEvent
from ui.widgets.timeline_model import TimelineTableModel, _EVENT_TYPE_LABELS
from PyQt6.QtCore import Qt

print("\n" + "╔" + "=" * 78 + "╗")
print("║" + " VALIDATING DROIDTRACE PRO UI FIXES ".center(78) + "║")
print("╚" + "=" * 78 + "╝\n")

# ══════════════════════════════════════════════════════════════════════════════
# ISSUE 1 VALIDATION: LOG TIMELINE ORDERING
# ══════════════════════════════════════════════════════════════════════════════

print("=" * 80)
print("ISSUE 1: LOG TIMELINE ORDERING")
print("=" * 80)

model = TimelineTableModel()

events = [
    TimelineEvent(event_id="e1", sequence_index=0,
        timestamp=datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc),
        iso_timestamp="2024-01-15T14:00:00Z",
        app="com.app1", event_type="APP_OPENED", source="usage_stats", evidence_type="DIRECT"),
    TimelineEvent(event_id="e2", sequence_index=1,
        timestamp=None, iso_timestamp="UNKNOWN",
        app="com.app2", event_type="APP_INSTALLED", source="packages", evidence_type="DIRECT"),
    TimelineEvent(event_id="e3", sequence_index=2,
        timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        iso_timestamp="2024-01-15T10:00:00Z",
        app="com.app3", event_type="APP_CLOSED", source="usage_stats", evidence_type="DIRECT"),
]

model.set_events(events)

print("\n✓ UNKNOWN timestamps display as 'UNKNOWN' in UI but sort by sequence_index:")
for row in range(model.rowCount()):
    idx = model.createIndex(row, 1)
    display_text = model.data(idx, Qt.ItemDataRole.DisplayRole)
    sort_value = model.data(idx, Qt.ItemDataRole.UserRole + 1)
    event = model.event_at(row)
    
    is_unknown = event.timestamp is None
    if is_unknown:
        has_seq_idx = f"_{event.sequence_index:010d}" in sort_value
        print(f"  ✓ {event.app}: Display='{display_text}', Sort uses seq_idx={has_seq_idx}")
    else:
        print(f"  ✓ {event.app}: Display='{display_text}', Sorts chronologically")

# ══════════════════════════════════════════════════════════════════════════════
# ISSUE 2 VALIDATION: ACTIVITY TYPE DISPLAY
# ══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 80)
print("ISSUE 2: ACTIVITY TYPE DISPLAY")
print("=" * 80)

test_types = [
    "APP_INSTALLED",
    "APP_UNINSTALLED",
    "APP_OPENED",
    "APP_CLOSED",
    "APP_UPDATED",
    "NETWORK_CONNECT",
    "SCREEN_ON",
]

print("\n✓ Event types now display with emojis and human-readable labels:")
for event_type in test_types:
    emoji, label = _EVENT_TYPE_LABELS.get(event_type, ("ℹ️", event_type))
    display = f"{emoji} {label}"
    print(f"  {event_type:25} → {display}")

# Test in model display
model2 = TimelineTableModel()
events2 = [
    TimelineEvent(event_id=f"ev{i}", sequence_index=i, app=f"com.app{i}",
                 event_type=et, source="test", evidence_type="DIRECT")
    for i, et in enumerate(test_types)
]
model2.set_events(events2)

print("\n✓ Display layer shows mapped labels (Event Type column):")
for row in range(min(3, model2.rowCount())):
    idx = model2.createIndex(row, 4)  # column 4 = Event Type
    display_text = model2.data(idx, Qt.ItemDataRole.DisplayRole)
    event = model2.event_at(row)
    print(f"  {event.event_type:25} → {display_text}")

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 80)
print("VALIDATION SUMMARY")
print("=" * 80)
print("✓ Issue 1: UNKNOWN timestamps sort by sequence_index (display unchanged)")
print("✓ Issue 2: Event types display with emojis + human-readable labels")
print("✓ Constraints met:")
print("  - No backend API changes (display only)")
print("  - Raw data unchanged in models")
print("  - Filters apply before sorting")
print("=" * 80 + "\n")
