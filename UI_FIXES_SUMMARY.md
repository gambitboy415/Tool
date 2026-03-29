# DroidTrace Pro UI Fixes — Summary

## Overview

Fixed two critical issues in the event log/timeline view:

1. **Issue 1**: Events with UNKNOWN timestamps didn't sort correctly
2. **Issue 2**: All events displayed generic "APP_INSTALLED" without activity context

---

## Issue 1: LOG TIMELINE ORDERING

### Problem

- Events with missing/null/UNKNOWN timestamps weren't maintaining logical order
- Users couldn't tell the sequence of events without valid timestamps
- Sorting was random for events without timestamps

### Solution

- **Display**: Shows "UNKNOWN" in the Timestamp column for events without valid timestamps
- **Sorting**: UNKNOWN timestamps internally use `sequence_index` as a fallback for sorting
- **Behavior**:
  - Valid timestamps sort chronologically (ISO format string comparison)
  - UNKNOWN timestamps sort by their `sequence_index` (e.g., "UNKNOWN_0000000001")
  - Valid timestamps appear first, UNKNOWN timestamps grouped at the end
  - Original relative order preserved for events with no timestamp

### Files Modified

- **`ui/widgets/timeline_model.py`**:
  - Added custom sort role (UserRole + 1) in `data()` method
  - For timestamp column, returns `"UNKNOWN_{sequence_index:010d}"` for events without timestamps
  - Returns ISO string for events with valid timestamps

- **`ui/timeline_view.py`**:
  - Added `lessThan()` method to `_EvidenceFilterProxy` class
  - Implements custom comparison logic for timestamp column (column 1)
  - Uses the custom sort role for proper ordering
  - Ensures sorting applies AFTER filtering (standard Qt behavior)

### Implementation Details

```python
# Custom sort role example:
# Valid timestamp: "2024-01-15T10:00:00Z"
# UNKNOWN timestamp: "UNKNOWN_0000000001"

# String comparison order:
# "2024..." < "UNKNOWN_..." (chronological events appear first)
```

---

## Issue 2: ACTIVITY TYPE DISPLAY

### Problem

- All events showed raw event type (e.g., "APP_OPENED", "APP_INSTALLED")
- No visual distinction or human-friendly labels
- Users had to memorize event type codes

### Solution

- **Mapping**: Event types mapped to emoji + human-readable labels:
  - `APP_INSTALLED` → `📦 Installed`
  - `APP_UNINSTALLED` → `🗑️ Uninstalled`
  - `APP_OPENED` → `🟢 App Opened`
  - `ACTIVITY_RESUMED` → `🟢 App Opened` (alias)
  - `APP_CLOSED` → `🔴 App Closed`
  - `ACTIVITY_PAUSED` → `🔴 App Closed` (alias)
  - `APP_UPDATED` → `🔄 Updated`
  - `SCREEN_ON` → `💡 Screen On`
  - `SCREEN_OFF` → `⚫ Screen Off`
  - `NETWORK_CONNECT` → `🌐 Network Connected`
  - `NETWORK_DISCONNECT` → `❌ Network Disconnected`
  - Unknown types → `ℹ️ [EVENT_TYPE]` (fallback)

- **Display**: Event Type column shows mapped label + emoji
- **Backend**: Raw `event_type` field unchanged in data model

### Files Modified

- **`ui/widgets/timeline_model.py`**:
  - Added `_EVENT_TYPE_LABELS` dictionary mapping event types to (emoji, label) tuples
  - Modified `_display()` method for column 4 (Event Type):
    ```python
    case 4:
        emoji, label = _EVENT_TYPE_LABELS.get(event.event_type, ("ℹ️", event.event_type))
        return f"{emoji} {label}"
    ```

---

## Constraints Met ✓

- ✓ **Zero backend changes**: Only UI display layer modified
- ✓ **No API field modifications**: Raw data model fields unchanged
- ✓ **Filters still work**: All existing filters (All/Direct/Correlated/Inferred/Flagged) unaffected
- ✓ **Sort after filter**: Sorting applies after filters, not before (Qt standard behavior)
- ✓ **Data integrity**: Events never discarded, original sequence preserved

---

## Testing Verification

All fixes validated with comprehensive tests:

### Test 1: Event Type Mapping

- ✓ All mapped event types display correctly with emoji + label
- ✓ Unmapped types use generic fallback
- ✓ Display appears in table column 4 (Event Type)

### Test 2: UNKNOWN Timestamp Sorting

- ✓ Events display "UNKNOWN" in UI
- ✓ Internally sorted by sequence_index
- ✓ Chronological events appear before UNKNOWN events
- ✓ Relative order preserved

### Test 3: Custom Sorting (lessThan)

- ✓ lessThan method correctly compares sort values
- ✓ Valid timestamps sort correctly as ISO strings
- ✓ UNKNOWN timestamps sorted by sequence_index
- ✓ Overall sort order: chronological first, then by sequence

### Test 4: Filter + Sort Integration

- ✓ Filters applied before sorting
- ✓ Filtered events maintain correct sort order
- ✓ No data loss or corruption

---

## User Impact

### Before Fixes

```
# Timeline was confusing:
#         Timestamp        Event Type   App
Row 1:    2024-01-15...    APP_OPENED   com.app1
Row 2:    UNKNOWN          APP_OPENED   com.app2     ← Hard to tell order
Row 3:    2024-01-16...    APP_OPENED   com.app3
Row 4:    UNKNOWN          APP_OPENED   com.app4     ← Grouped randomly
```

### After Fixes

```
# Timeline is clear and organized:
#         Timestamp        Activity Type        App
Row 1:    2024-01-15...    🟢 App Opened       com.app1
Row 2:    2024-01-16...    🟢 App Opened       com.app3
Row 3:    UNKNOWN          📦 Installed        com.app2    ← Clear sequence
Row 4:    UNKNOWN          📦 Installed        com.app4    ← Sorted by order
```

---

## Technical Notes

### Why String Comparison Works for Sorting

- ISO 8601 format (`2024-01-15T10:00:00Z`) sorts correctly as strings because dates come first
- Timestamps like `"2024-01-15T10:00:00Z"` < `"2024-01-15T12:00:00Z"` alphabetically ✓
- UNKNOWN format `"UNKNOWN_0000000001"` < `"UNKNOWN_0000000003"` ✓
- The leading digit "2" sorts before letter "U", so valid timestamps come first ✓

### Why Custom lessThan is Needed

- Qt's default sorting would sort by display text (all "UNKNOWN" alphabetically)
- Custom lessThan uses UserRole + 1 for proper ordering
- Ensures sequence_index is used as tiebreaker for UNKNOWN timestamps
- Maintains chronological order when user clicks column header to sort

---

## Future Enhancements (Optional)

- Add user preference to show/hide UNKNOWN timestamps
- Add timestamp validation indicators
- Add batch operations on filtered events
- Export filtered timeline to timeline visualization
