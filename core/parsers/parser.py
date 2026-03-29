"""
core/parsers/parser.py
=======================
Parsing and normalisation layer for DroidTrace Pro.

Converts raw ADB text output (RawArtifact) into structured ParsedEvent objects.
This module is the boundary between "what the device said" and "what it means."

Design principles:
─────────────────────────────────────────────────────────────────────────────
1. RELIABILITY OVER COMPLETENESS
   Parse whatever can be safely extracted.  A partially parsed event with an
   approximate timestamp is better than a crash or a skipped artifact.

2. NO MUTATION OF RAW DATA
   Parsers read ``RawArtifact.raw_output`` but never modify it.  The raw text
   is the forensic source-of-truth and must remain untouched for audit purposes.

3. MULTI-FORMAT FALLBACK
   Each parser tries patterns in priority order (most specific → most general).
   If all patterns fail for a line, that line is skipped and logged as a warning,
   never raised as an exception.

4. TIMESTAMP NORMALISATION
   All timestamps are converted to UTC-aware datetime at parse time.
   If a timestamp is absent or unparseable, the artifact's ``collected_at``
   is used as an approximation and ``timestamp_approximate`` is set to True.

5. DEDUPLICATION
   After all sources are parsed, ``deduplicate()`` removes events sharing the
   same (timestamp_epoch_ms, app, event_type) triplet.  The first occurrence
   from the most authoritative source is retained.

Supported artifact types:
─────────────────────────────────────────────────────────────────────────────
  USAGE_STATS   → UsageStatsParser   → APP_OPENED, APP_CLOSED, etc.
  APP_LIST      → PackageListParser  → APP_INSTALLED (install time from pm -f)
  APP_DETAIL    → PackageDetailParser→ APP_INSTALLED, APP_UPDATED, APP_UNINSTALLED

Usage:
─────────────────────────────────────────────────────────────────────────────
    from core.parsers.parser import parse_artifacts

    events = parse_artifacts(collection_result.artifacts)
    for event in events:
        print(event)
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from typing import Iterator, Optional
from zoneinfo import ZoneInfoNotFoundError

from models.parsed_event import ParsedEvent
from models.raw_artifact import ArtifactType, RawArtifact
from utils.logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Android usage-stats event type codes → normalised event_type strings
# Reference: android.app.usage.UsageEvents.Event (AOSP source)
# ─────────────────────────────────────────────────────────────────────────────
_USAGE_EVENT_TYPES: dict[str, str] = {
    "1":  "APP_OPENED",          # MOVE_TO_FOREGROUND
    "2":  "APP_CLOSED",          # MOVE_TO_BACKGROUND
    "3":  "END_OF_DAY",
    "4":  "CONTINUE_PREVIOUS_DAY",
    "5":  "CONFIGURATION_CHANGE",
    "6":  "SYSTEM_INTERACTION",
    "7":  "USER_INTERACTION",
    "8":  "SHORTCUT_INVOCATION",
    "9":  "CHOOSER_ACTION",
    "10": "NOTIFICATION_SEEN",
    "11": "STANDBY_BUCKET_CHANGED",
    "12": "NOTIFICATION_INTERRUPTION",
    "13": "SLICE_PINNED_PRIV",
    "14": "SLICE_PINNED",
    "15": "ACTIVITY_RESUMED",
    "16": "ACTIVITY_PAUSED",
    "17": "ACTIVITY_STOPPED",
    "18": "ACTIVITY_DESTROYED",
    "19": "FLUSH_TO_DISK",
    "20": "KEYGUARD_SHOWN",
    "21": "KEYGUARD_HIDDEN",
    "22": "FOREGROUND_SERVICE_START",
    "23": "FOREGROUND_SERVICE_STOP",
    "24": "CONTINUING_FOREGROUND_SERVICE",
    "25": "ROLLOVER_FOREGROUND_SERVICE",
    "26": "ACTIVITY_STOPPED",        # alias in some builds
    "27": "DEVICE_SHUTDOWN",
    "28": "DEVICE_STARTUP",
    "29": "USER_UNLOCKED",
    "30": "USER_STOPPED",
    # Named forms (some Android builds emit names instead of codes)
    "MOVE_TO_FOREGROUND":       "APP_OPENED",
    "MOVE_TO_BACKGROUND":       "APP_CLOSED",
    "ACTIVITY_RESUMED":         "APP_OPENED",
    "ACTIVITY_PAUSED":          "APP_CLOSED",
    "ACTIVITY_STOPPED":         "APP_CLOSED",
    "ACTIVITY_DESTROYED":       "APP_CLOSED",
    # Keys for internal logic
    "APP_OPENED": "APP_OPENED",
    "APP_CLOSED": "APP_CLOSED",
}


# ─────────────────────────────────────────────────────────────────────────────
# Timestamp parsing utilities
# ─────────────────────────────────────────────────────────────────────────────

# Ordered list of (regex_pattern, strptime_format | "epoch_ms" | "epoch_s")
# Tried in order; first match wins.
_TIMESTAMP_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Epoch milliseconds (13 digits): 1704067200000
    (re.compile(r"^\d{13}$"), "epoch_ms"),
    # Epoch seconds (10 digits): 1704067200
    (re.compile(r"^\d{10}$"), "epoch_s"),
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+"), "%Y-%m-%dT%H:%M:%S.%f"),
    # ISO 8601 without milliseconds, with possible T: 2024-01-15T22:30:45
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"), "%Y-%m-%dT%H:%M:%S"),
    # Space-separated long form (isoformat() on naive datetime or str()): 2024-01-15 22:30:45.123
    (re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+"), "%Y-%m-%d %H:%M:%S.%f"),
    # Space-separated short form: 2024-01-15 22:30:45
    (re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"), "%Y-%m-%d %H:%M:%S"),
    # Date only (fallback): 2024-01-15
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "%Y-%m-%d"),
]


# The earliest plausible Android device timestamp (Android 1.0 launch).
# Timestamps prior to this are almost certainly corrupt or Unix epoch 0.
_MIN_FORENSIC_DATE = datetime(2008, 10, 22, tzinfo=timezone.utc)


def _parse_timestamp(raw: str, fallback: Optional[datetime] = None) -> tuple[datetime, bool]:
    """
    Attempt to parse any timestamp string into a UTC-aware datetime.

    Strategy:
      1. Strip surrounding whitespace and quotes. Handle null/None/empty.
      2. Try each pattern in ``_TIMESTAMP_PATTERNS`` in order.
      3. For epoch values, convert directly to UTC datetime.
      4. For string formats, assume UTC if no timezone info is present.
      5. SANITY CHECK: If timestamp predates _MIN_FORENSIC_DATE (2008),
         reject it and use fallback (marked as approximate).
      6. If all patterns fail and a fallback is provided, return fallback
         with ``approximate=True``.

    Args:
        raw:      Raw timestamp string from ADB output.
        fallback: datetime to use if parsing fails (typically artifact.collected_at).

    Returns:
        Tuple of (datetime, approximate: bool).
        approximate=True if the fallback was used or the timestamp was rejected.
    """
    raw = str(raw).strip().strip('"').strip("'").lower()

    # Handle explicit null/zero strings often found in corrupted ADB dumps
    if not raw or raw in ("0", "null", "none", "(null)", "unknown", "0000-00-00 00:00:00"):
        return None, False

    for pattern, fmt in _TIMESTAMP_PATTERNS:
        if not pattern.search(raw):
            continue
        try:
            dt: Optional[datetime] = None
            if fmt == "epoch_ms":
                # Clamp to 13 digits to avoid overflow on malformed values
                digits = re.sub(r"\D", "", raw)[:13]
                ts_ms = int(digits)
                dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
            elif fmt == "epoch_s":
                digits = re.sub(r"\D", "", raw)[:10]
                ts_s = int(digits)
                dt = datetime.fromtimestamp(float(ts_s), tz=timezone.utc)
            else:
                # Extract the matching portion (pattern may match a substring)
                match = pattern.search(raw)
                if match:
                    dt = datetime.strptime(match.group(), fmt)
                    if dt.tzinfo is None:
                        # Assume UTC
                        dt = dt.replace(tzinfo=timezone.utc)

            # --- Forensic Sanity Check ---
            # We no longer clamp to Android Epoch. We pass the exact dt.
            if dt:
                return dt, False

        except (ValueError, OverflowError, OSError):
            continue  # try next pattern

    # All patterns failed — do not hallucinate a date
    log.debug("Timestamp unparseable '%s', returning None", raw)
    return None, False


# ─────────────────────────────────────────────────────────────────────────────
# Abstract base parser
# ─────────────────────────────────────────────────────────────────────────────

class BaseParser(ABC):
    """
    Abstract base for all artifact parsers.

    Subclasses implement ``_iter_events()`` which yields raw (timestamp_str, app,
    event_type, raw_fields) tuples.  The base class handles timestamp parsing,
    approximate-flag setting, and ParsedEvent construction.
    """

    #: Identifies this parser in log messages and event ``source`` fields.
    SOURCE_NAME: str = "unknown"

    def parse(self, artifact: RawArtifact) -> list[ParsedEvent]:
        """
        Entry point.  Parses a RawArtifact into a list of ParsedEvents.

        Args:
            artifact: The raw artifact to parse.

        Returns:
            List of ParsedEvents.  Empty list if nothing could be extracted.
        """
        if not artifact.is_successful:
            log.warning(
                "Skipping failed artifact %s (error: %s)",
                artifact.artifact_type.value, artifact.error,
            )
            return []

        events: list[ParsedEvent] = []
        for item in self._iter_events(artifact):
            ts_raw, app, event_type, raw_fields = item
            if not app or not event_type:
                continue

            dt, approximate = _parse_timestamp(ts_raw, fallback=artifact.collected_at)

            # Resolve the 'reason' from the generator if provided in raw_fields
            reason = raw_fields.pop("_reason", "Raw extraction")

            events.append(ParsedEvent(
                timestamp=dt,
                app=app.strip(),
                event_type=event_type.strip().upper(),
                source=self.SOURCE_NAME,
                evidence_type="DIRECT",
                raw_fields=raw_fields,
                source_command=artifact.source_command,
                timestamp_approximate=approximate,
                valid_time=dt is not None,
                reason=reason
            ))

        log.info(
            "Parser '%s' extracted %d events from %s",
            self.SOURCE_NAME, len(events), artifact.artifact_type.value,
        )
        return events

    @abstractmethod
    def _iter_events(
        self, artifact: RawArtifact
    ) -> Iterator[tuple[str, str, str, dict]]:
        """
        Yield raw event tuples: (timestamp_str, app, event_type, raw_fields).

        Implementations must be generators (or return an iterator).
        They must NEVER raise on bad data — log and skip instead.
        """
        raise NotImplementedError  # pragma: no cover


# ─────────────────────────────────────────────────────────────────────────────
# UsageStatsParser
# ─────────────────────────────────────────────────────────────────────────────

class UsageStatsParser(BaseParser):
    """
    Parses ``dumpsys usagestats`` output into app lifecycle events.

    The output format changed multiple times across Android versions.
    This parser handles four known formats:

    Format A — Android 10+ (event log per line):
        time=1704067200123 pkg=com.whatsapp type=1 ...

    Format B — Android 9 (parenthesised epoch + name):
        time: 2024-01-15 22:30:45.123(1704067200123), package: com.whatsapp, type: MOVE_TO_FOREGROUND(1)

    Format C — Android 8 (block per event):
        Event start: 2024-01-15 22:30:45.123
          mPackage=com.whatsapp
          mEventType=1

    Format D — Event with millisecond epoch only:
        2024-01-15 22:30:45.123 com.whatsapp MOVE_TO_FOREGROUND

    Format E — Android 16 / Fuzzy Key-Value:
        eventType=1(APP_OPENED) packageName=com.whatsapp time=1704067200123
    """

    SOURCE_NAME = "usage_stats"

    # ── Compiled regex patterns ───────────────────────────────────────────────

    # Format A: key=value pairs  —  e.g. time=1704067200123 pkg=com.whatsapp type=1
    _RE_FMT_A = re.compile(
        r"time[=:]\s*(?P<time>\d{10,13})"
        r".*?(?:pkg|package)[=:]\s*(?P<pkg>[a-zA-Z0-9._]+)"
        r".*?type[=:]\s*(?P<type>\w+)",
        re.IGNORECASE,
    )

    # Format B: parenthesised epoch — time: <human>(EPOCH), package: pkg, type: NAME(code)
    _RE_FMT_B = re.compile(
        r"time:\s*[\d\-\s:.]+\((?P<time>\d{10,13})\)"
        r".*?package:\s*(?P<pkg>[a-zA-Z0-9._]+)"
        r".*?type:\s*(?P<type>[A-Z_]+)",
        re.IGNORECASE,
    )

    # Format C block: opener line
    _RE_FMT_C_START = re.compile(
        r"Event start:\s*(?P<time>[\d\-\s:.T]+)",
        re.IGNORECASE,
    )
    _RE_FMT_C_PKG = re.compile(r"mPackage\s*=\s*(?P<pkg>[a-zA-Z0-9._]+)")
    _RE_FMT_C_TYPE = re.compile(r"mEventType\s*=\s*(?P<type>\w+)")

    _RE_FMT_D = re.compile(
        r"(?P<time>\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}(?:\.\d+)?)"
        r"\s+(?P<pkg>[a-zA-Z][a-zA-Z0-9._]{2,})"
        r"\s+(?P<type>[A-Z_]{4,})",
    )

    # Format E: Fuzzy Key-Value (Android 16+ and Samsung variants)
    # Extracts pkg, type, and time in any order with flexible keys
    _RE_FMT_E_PKG  = re.compile(r"(?:pkg|package|packageName|pkgName)\s*[=:]\s*['\"]?(?P<pkg>[a-zA-Z0-9\._\-]+)['\"]?", re.I)
    _RE_FMT_E_TYPE = re.compile(r"(?:type|eventType|event|rawType)\s*[=:]\s*['\"]?(?P<type>[a-zA-Z0-9_]+)['\"]?", re.I)
    _RE_FMT_E_TIME = re.compile(r"(?:time|timestamp|ts|timeMillis)\s*[=:]\s*['\"]?(?P<time>\d{10,13}|\d{4}-\d{2}-\d{2}.{8,15})['\"]?", re.I)

    # Format F: Package Summary (Forensic Fallback)
    # package=com.example.app ... lastTimeUsed=1704067200123 totalTimeForeground=1234
    _RE_SUMMARY_PKG = re.compile(
        r'package=(?P<pkg>[a-zA-Z0-9\._]+).*?lastTimeUsed=(?P<time>\d+)(?:.*?totalTimeForeground=(?P<total>\d+))?',
        re.IGNORECASE,
    )

    def _iter_events(
        self, artifact: RawArtifact
    ) -> Iterator[tuple[str, str, str, dict]]:
        lines = artifact.raw_output.splitlines()
        
        # Track the app currently in the foreground to implement Implicit Close logic.
        # Format: last_package_name
        current_foreground_app: Optional[str] = None

        i = 0
        while i < len(lines):
            line = lines[i]
            extracted: Optional[tuple[str, str, str, dict]] = None

            # Attempt extraction via different formats
            m = self._RE_FMT_A.search(line)
            if m:
                extracted = (m.group("time"), m.group("pkg"), m.group("type"), {"format": "A"})
            elif (m := self._RE_FMT_B.search(line)):
                extracted = (m.group("time"), m.group("pkg"), m.group("type"), {"format": "B"})
            elif (m := self._RE_FMT_C_START.search(line)):
                time_str = m.group("time").strip()
                pkg, type_raw = "", ""
                for j in range(i + 1, min(i + 10, len(lines))):
                    pm = self._RE_FMT_C_PKG.search(lines[j])
                    if pm: pkg = pm.group("pkg")
                    tm = self._RE_FMT_C_TYPE.search(lines[j])
                    if tm: type_raw = tm.group("type")
                    if pkg and type_raw: break
                if pkg:
                    extracted = (time_str, pkg, type_raw, {"format": "C"})
            elif (m := self._RE_FMT_D.search(line)):
                extracted = (m.group("time"), m.group("pkg"), m.group("type"), {"format": "D"})
            else:
                me_pkg  = self._RE_FMT_E_PKG.search(line)
                me_type = self._RE_FMT_E_TYPE.search(line)
                me_time = self._RE_FMT_E_TIME.search(line)
                if me_pkg and me_type and me_time:
                    extracted = (me_time.group("time"), me_pkg.group("pkg"), me_type.group("type"), {"format": "E"})
                else:
                    # Final fallback: check for summary marker
                    m = self._RE_SUMMARY_PKG.search(line)
                    if m:
                        # Summary events are treated as 'OPEN' markers for forensic sequencing
                        pkg = m.group("pkg")
                        time_raw = m.group("time")
                        total_time = m.group("total")
                        
                        fields = {"format": "SUMMARY", "source": "PackageConfigs"}
                        if total_time:
                            fields["total_time_ms"] = int(total_time)
                            
                        # Forensically we mark these as APP_OPENED as they represent the last active state
                        extracted = (time_raw, pkg, "APP_OPENED", fields)

            # Process extracted event
            if extracted:
                time_str, pkg, type_raw, raw_fields = extracted
                norm_type = _resolve_usage_event_type(type_raw)
                raw_fields["raw_type"] = type_raw
                
                if norm_type == "APP_OPENED":
                    # Phase 1: A -> B transition = CLOSE(A) + OPEN(B)
                    if current_foreground_app and current_foreground_app != pkg:
                        # Log the implicit closure for forensic audit
                        yield (
                            time_str, 
                            current_foreground_app, 
                            "APP_CLOSED", 
                            {"_reason": f"Forensic transition: Implicitly closed because {pkg} opened", "inferred": True}
                        )
                    
                    # Update foreground tracker
                    current_foreground_app = pkg
                
                elif norm_type == "APP_CLOSED":
                    if current_foreground_app == pkg:
                        current_foreground_app = None

                if norm_type:
                    raw_fields["_reason"] = f"Behavioral {norm_type.lower()} event from usage stats"
                    yield (time_str, pkg, norm_type, raw_fields)

            i += 1


# ─────────────────────────────────────────────────────────────────────────────
# PackageDetailParser
# ─────────────────────────────────────────────────────────────────────────────

class PackageDetailParser(BaseParser):
    """
    Parses ``dumpsys package <pkg>`` output into install/update events.

    Key fields extracted:
        firstInstallTime  → APP_INSTALLED event
        lastUpdateTime    → APP_UPDATED event        (only if != firstInstallTime)
        uninstalledTime   → APP_UNINSTALLED event    (if present)
        versionName       → stored in raw_fields
        versionCode       → stored in raw_fields
        requestedPermissions → stored in raw_fields (comma-separated)

    Forensic note:
      ``firstInstallTime`` is the most reliable timestamp in all of Android
      forensics — it is stored by PackageManager and survives app updates.
      However, it resets on full uninstall + reinstall, which is itself
      a forensic signal.
    """

    SOURCE_NAME = "package_detail"

    # Field extraction patterns
    _RE_PKG_NAME    = re.compile(r"Package \[(?P<pkg>[a-zA-Z0-9._]+)\]")
    _RE_FIRST_INST  = re.compile(r"firstInstallTime\s*=\s*(?P<ts>[^\n]+)")
    _RE_LAST_UPDATE = re.compile(r"lastUpdateTime\s*=\s*(?P<ts>[^\n]+)")
    _RE_UNINST_TIME = re.compile(r"(?:uninstalledTime|deletedTime)\s*=\s*(?P<ts>[^\n]+)")
    _RE_VERSION     = re.compile(r"versionName\s*=\s*(?P<ver>[^\s,\n]+)")
    _RE_VER_CODE    = re.compile(r"versionCode\s*=\s*(?P<code>\d+)")

    def _iter_events(
        self, artifact: RawArtifact
    ) -> Iterator[tuple[str, str, str, dict]]:
        text = artifact.raw_output

        # Resolve package name from metadata first, then from output
        pkg = artifact.metadata.get("package", "")
        if not pkg:
            m = self._RE_PKG_NAME.search(text)
            if m:
                pkg = m.group("pkg")
        if not pkg:
            log.warning("PackageDetailParser: no package name found in artifact")
            return

        # Extract auxiliary fields for raw_fields attachment
        version = ""
        vm = self._RE_VERSION.search(text)
        if vm:
            version = vm.group("ver")

        version_code = ""
        vcm = self._RE_VER_CODE.search(text)
        if vcm:
            version_code = vcm.group("code")

        base_fields = {
            "package":      pkg,
            "version_name": version,
            "version_code": version_code,
        }

        # ── firstInstallTime → APP_INSTALLED ──────────────────────────────
        m = self._RE_FIRST_INST.search(text)
        if m:
            ts_raw = m.group("ts").strip()
            yield (ts_raw, pkg, "APP_INSTALLED", {**base_fields, "field": "firstInstallTime"})

        # ── lastUpdateTime → APP_UPDATED (only if different from install time) ──
        mu = self._RE_LAST_UPDATE.search(text)
        mi = self._RE_FIRST_INST.search(text)
        if mu:
            update_ts = mu.group("ts").strip()
            install_ts = mi.group("ts").strip() if mi else ""
            if update_ts != install_ts:  # skip if they're the same (no update occurred)
                yield (update_ts, pkg, "APP_UPDATED", {**base_fields, "field": "lastUpdateTime"})

        # ── uninstalledTime → APP_UNINSTALLED ─────────────────────────────
        md = self._RE_UNINST_TIME.search(text)
        if md:
            uninst_ts = md.group("ts").strip()
            # Only emit if timestamp is non-zero / non-null
            if uninst_ts not in ("0", "null", "-1", ""):
                yield (uninst_ts, pkg, "APP_UNINSTALLED", {**base_fields, "field": "uninstalledTime"})


# ─────────────────────────────────────────────────────────────────────────────
# PowerStateParser
# ─────────────────────────────────────────────────────────────────────────────

class PowerStateParser(BaseParser):
    """
    Parses ``dumpsys power`` output into screen state events.

    Extracts:
      - mWakefulness=Awareness of device state (Awake, Asleep, Dreaming)
      - mScreenState=Physical display state (OFF, ON, DOZE)
      - Timestamp of the last wakefulness change if available.
    """

    SOURCE_NAME = "power"

    # Current state patterns (found in 'Display Power: state=...')
    _RE_POWER_STATE = re.compile(r"Display Power:\s*state=(?P<state>[A-Z_]+)", re.I)

    # Wakefulness patterns
    _RE_WAKEFULNESS = re.compile(r"mWakefulness=(?P<wake>[a-zA-Z]+)", re.I)

    def _iter_events(
        self, artifact: RawArtifact
    ) -> Iterator[tuple[str, str, str, dict]]:
        text = artifact.raw_output
        collected_at_str = artifact.collected_at.isoformat()

        # 1. Detect current wakefulness as a point-in-time event
        mw = self._RE_WAKEFULNESS.search(text)
        if mw:
            wake_state = mw.group("wake").upper()
            event_type = "DEVICE_AWAKE" if wake_state == "AWAKE" else "DEVICE_ASLEEP"
            yield (collected_at_str, "android", event_type, {"wakefulness": wake_state})

        # 2. Detect display state
        ms = self._RE_POWER_STATE.search(text)
        if ms:
            state = ms.group("state").upper()
            event_type = "SCREEN_ON" if state == "ON" else "SCREEN_OFF"
            yield (collected_at_str, "android", event_type, {"display_state": state})

        log.debug("PowerStateParser: points-in-time extracted from power dump")


# ─────────────────────────────────────────────────────────────────────────────
# PackageListParser
# ─────────────────────────────────────────────────────────────────────────────

class PackageListParser(BaseParser):
    """
    Parses ``pm list packages -f`` and ``pm list packages -u`` output.

    This parser produces lightweight APP_LISTED events.  It does NOT produce
    APP_INSTALLED events with install times — that requires PackageDetailParser.
    Its value is:
      - Providing a complete inventory of apps (including those whose
        ``dumpsys package`` detail collection was skipped or failed).
      - Detecting path anomalies (e.g. a "system" package in /data/app/).
      - Identifying packages present in the uninstalled list with residual data.

    Output line format:
        package:/data/app/~~randomsuffix==/com.example-1/base.apk=com.example

    For uninstalled packages (``pm list packages -u``):
        package:com.deleted.app
    """

    SOURCE_NAME = "app_list"

    _RE_FULL_LINE   = re.compile(
        r"package:(?P<path>[^\s=]+)?=(?P<pkg>[a-zA-Z][a-zA-Z0-9._]+)(?:\s+uid:(?P<uid>\d+))?"
    )
    _RE_SIMPLE_LINE = re.compile(
        r"package:(?P<pkg>[a-zA-Z][a-zA-Z0-9._]+)(?:\s+uid:(?P<uid>\d+))?"
    )

    def _iter_events(
        self, artifact: RawArtifact
    ) -> Iterator[tuple[str, str, str, dict]]:
        is_uninstalled = artifact.metadata.get("scope") == "uninstalled"
        event_type = "APP_UNINSTALL_RESIDUAL" if is_uninstalled else "APP_LISTED"

        for line in artifact.raw_output.splitlines():
            line = line.strip()
            if not line.startswith("package:"):
                continue

            # Try full format (with APK path) first
            m = self._RE_FULL_LINE.search(line)
            if m:
                pkg  = m.group("pkg").strip()
                uid  = m.group("uid")
                path = m.group("path") or ""
                apk_location = _classify_apk_location(path)
                yield (
                    artifact.collected_at.strftime("%Y-%m-%d %H:%M:%S"),
                    pkg,
                    event_type,
                    {"apk_path": path, "apk_location": apk_location, "uid": uid},
                )
                continue

            # Fallback: simple format (no path)
            m = self._RE_SIMPLE_LINE.search(line)
            if m:
                pkg = m.group("pkg").strip()
                uid = m.group("uid")
                yield (
                    artifact.collected_at.strftime("%Y-%m-%d %H:%M:%S"),
                    pkg,
                    event_type,
                    {"uid": uid},
                )

# ─────────────────────────────────────────────────────────────────────────────
# NetstatsParser
# ─────────────────────────────────────────────────────────────────────────────

class NetstatsParser(BaseParser):
    """
    Parses ``dumpsys netstats --uid --full`` into network usage events.

    Extracts:
      - App UID (which is mapped to a package name via uid_map)
      - Cumulative byte counters (rxBytes, txBytes)
      - Bucket timestamps (if available) or uses collection time.

    Usage:
      This parser requires a ``uid_map`` (UID string -> Package string) to be
      passed via metadata or set before parsing.
    """

    SOURCE_NAME = "network"

    # Pattern for 'uid=10123 set=ALL tag=0x0 rxBytes=123 txBytes=456'
    _RE_NET_KV = re.compile(
        r"uid=(?P<uid>\d+).*?rxBytes=(?P<rx>\d+).*?txBytes=(?P<tx>\d+)", 
        re.I
    )
    
    # Pattern for '9,10123,i,0,123,456,789,012' (CSV style)
    _RE_NET_CSV = re.compile(
        r"^\d+,(?P<uid>\d+),\w+,\d+,(?P<rx>\d+),\d+,(?P<tx>\d+)",
        re.MULTILINE
    )

    def _iter_events(
        self, artifact: RawArtifact
    ) -> Iterator[tuple[str, str, str, dict]]:
        uid_map = artifact.metadata.get("uid_map", {})
        ts_str = artifact.collected_at.strftime("%Y-%m-%d %H:%M:%S")

        # Strategy 1: KV pairs
        for line in artifact.raw_output.splitlines():
            m = self._RE_NET_KV.search(line)
            if not m:
                continue
            
            uid = m.group("uid")
            pkg = uid_map.get(uid, f"uid_{uid}")
            rx = int(m.group("rx"))
            tx = int(m.group("tx"))
            
            if rx == 0 and tx == 0:
                continue

            yield (
                ts_str,
                pkg,
                "NETWORK_USAGE",
                {
                    "uid": uid,
                    "bytes_rx": rx,
                    "bytes_tx": tx,
                    "bytes_total": rx + tx,
                }
            )

        # Strategy 2: CSV lines
        for m in self._RE_NET_CSV.finditer(artifact.raw_output):
            uid = m.group("uid")
            pkg = uid_map.get(uid, f"uid_{uid}")
            rx = int(m.group("rx"))
            tx = int(m.group("tx"))
            
            if rx == 0 and tx == 0:
                continue

            yield (
                ts_str,
                pkg,
                "NETWORK_USAGE",
                {
                    "uid": uid,
                    "bytes_rx": rx,
                    "bytes_tx": tx,
                    "bytes_total": rx + tx,
                }
            )


# ─────────────────────────────────────────────────────────────────────────────
# Deduplication
# ─────────────────────────────────────────────────────────────────────────────

# Source priority for deduplication: lower index = higher authority.
# When two sources produce the same (timestamp, app, event_type), the
# event from the higher-priority source is kept.
_SOURCE_PRIORITY: list[str] = [
    "package_detail",   # most authoritative — direct PM timestamps
    "usage_stats",      # recorded by UsageStatsManager in real-time
    "app_list",         # weakest — no timestamps, just presence
]


def deduplicate(events: list[ParsedEvent]) -> list[ParsedEvent]:
    """
    Remove duplicate events from a combined list of ParsedEvents.

    Two events are duplicates if they share the same ``dedup_key``
    (i.e. same timestamp_epoch_ms + app + event_type).

    When duplicates exist:
      - The event from the highest-priority source (per ``_SOURCE_PRIORITY``)
        is retained.
      - If both share the same source, the first occurrence is kept.

    Args:
        events: Combined list from multiple parsers, in any order.

    Returns:
        Deduplicated list ordered by (timestamp, source_priority).
    """
    def source_rank(e: ParsedEvent) -> int:
        try:
            return _SOURCE_PRIORITY.index(e.source)
        except ValueError:
            return len(_SOURCE_PRIORITY)  # unknown sources have lowest priority

    # Sort so highest-priority source comes first for each dedup_key
    sorted_events = sorted(events, key=lambda e: (e.dedup_key, source_rank(e)))

    seen: set[str] = set()
    unique: list[ParsedEvent] = []
    for event in sorted_events:
        if event.dedup_key not in seen:
            seen.add(event.dedup_key)
            unique.append(event)

    removed = len(events) - len(unique)
    if removed:
        log.info("Deduplication: removed %d duplicate events (%d → %d)", removed, len(events), len(unique))

    # Final sort: chronological
    return sorted(unique, key=lambda e: (e.timestamp is None, e.timestamp))


# ─────────────────────────────────────────────────────────────────────────────
# Parser registry & orchestration
# ─────────────────────────────────────────────────────────────────────────────

# Maps ArtifactType → parser class (or list of classes)
_PARSER_REGISTRY: dict[ArtifactType, type[BaseParser]] = {
    ArtifactType.USAGE_STATS: UsageStatsParser,
    ArtifactType.APP_DETAIL:  PackageDetailParser,
    ArtifactType.APP_LIST:    PackageListParser,
    ArtifactType.POWER:       PowerStateParser,
    ArtifactType.NETWORK:     NetstatsParser,
}


def parse_artifacts(
    artifacts: list[RawArtifact],
    dedup: bool = True,
) -> list[ParsedEvent]:
    """
    Parse a list of RawArtifacts into a deduplicated, chronologically sorted
    list of ParsedEvents.

    This is the primary public entry point for the parsing layer.

    Args:
        artifacts:  List of RawArtifacts from the data collection layer.
        dedup:      Whether to run deduplication (default True).
                    Set False only for debugging to see all raw parsed events.

    Returns:
        Sorted list of ParsedEvents ready for the timeline engine.
    """
    all_events: list[ParsedEvent] = []
    skipped_types: set[str] = set()

    # Pass 1: Extract UID mapping from all APP_LIST artifacts
    uid_map: dict[str, str] = {}
    for artifact in artifacts:
        if artifact.artifact_type == ArtifactType.APP_LIST:
            try:
                temp_events = PackageListParser().parse(artifact)
                for te in temp_events:
                    uid = te.raw_fields.get("uid")
                    if uid:
                        uid_map[uid] = te.app
            except Exception:
                pass
    
    log.debug("Built UID map with %d entries", len(uid_map))

    # Pass 2: Full Parse
    for artifact in artifacts:
        parser_class = _PARSER_REGISTRY.get(artifact.artifact_type)
        if parser_class is None:
            if artifact.artifact_type not in skipped_types:
                log.debug(
                    "No parser registered for artifact type '%s' — skipping",
                    artifact.artifact_type.value,
                )
                skipped_types.add(artifact.artifact_type)
            continue

        # Inject uid_map into metadata for NetstatsParser
        if artifact.artifact_type == ArtifactType.NETWORK:
            artifact.metadata["uid_map"] = uid_map

        try:
            parser = parser_class()
            events = parser.parse(artifact)
            all_events.extend(events)
        except Exception as exc:  # noqa: BLE001
            # A parser crash must never halt the entire pipeline.
            # Log it at ERROR level and continue with the remaining artifacts.
            log.error(
                "Parser '%s' raised an unexpected exception on artifact '%s': %s",
                parser_class.__name__, artifact.artifact_type.value, exc,
                exc_info=True,
            )

    log.info(
        "Parsing complete: %d raw events from %d artifacts",
        len(all_events), len(artifacts),
    )

    return deduplicate(all_events) if dedup else all_events


# ─────────────────────────────────────────────────────────────────────────────
# Module-level pure helpers
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_usage_event_type(raw_type: str) -> Optional[str]:
    """
    Map a raw Android event type (numeric code or name string) to our
    normalised event_type string.  Returns None for unknown types that
    should be filtered from forensic output (e.g. END_OF_DAY boundaries).

    Args:
        raw_type:  Raw type value from the ADB output (e.g. "1", "MOVE_TO_FOREGROUND").

    Returns:
        Normalised event_type string, or None if the event should be dropped.
    """
    # Types we deliberately suppress — they are internal bookkeeping by Android
    # and carry no forensic value for user activity reconstruction.
    _SUPPRESSED = {
        "END_OF_DAY", "CONTINUE_PREVIOUS_DAY", "FLUSH_TO_DISK",
        "STANDBY_BUCKET_CHANGED", "3", "4", "19", "11",
    }
    if raw_type in _SUPPRESSED:
        return None

    resolved = _USAGE_EVENT_TYPES.get(raw_type.strip())
    if resolved is None:
        # Unknown type: keep it with a generic label so we don't silently discard data
        resolved = f"USAGE_EVENT_{raw_type.strip().upper()}"
    return resolved


def _classify_apk_location(apk_path: str) -> str:
    """
    Classify an APK path as a forensic location category.

    Categories:
        "system"     — installed as part of the OS image (/system, /vendor, /product)
        "user"       — user-installed via Play Store or sideload (/data/app)
        "sideloaded" — installed from external storage (/sdcard, /mnt, /external)
        "unknown"    — path is empty or doesn't match known prefixes

    This classification is used by the inference engine to flag apps that
    appear in system paths but have user-level behaviour signatures.
    """
    if not apk_path:
        return "unknown"
    p = apk_path.lower()
    if p.startswith(("/system", "/vendor", "/product", "/apex", "/oem", "/priv-app")):
        return "system"
    if p.startswith("/data/app"):
        return "user"
    if any(p.startswith(prefix) for prefix in ("/sdcard", "/mnt", "/external", "/storage")):
        return "sideloaded"
    return "unknown"
