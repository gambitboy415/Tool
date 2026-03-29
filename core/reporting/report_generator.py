"""
core/reporting/report_generator.py
=====================================
Forensic Report Generator for DroidTrace Pro.

Orchestrates assembly of a complete forensic report from a finished timeline
and dispatches to the appropriate renderer (HTML or PDF).

Data collected per report:
  - Device identity (DeviceInfo)
  - Collection metadata (time, tool version)
  - Full timeline (all TimelineEvents)
  - Suspicious / flagged event summary
  - INFERRED event list
  - CORRELATED event groups
  - Per-rule behavioral pattern hits
  - Normalization and correlation statistics
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Literal, Optional

from config.settings import REPORT_OUTPUT_DIR
from core.reporting.html_renderer import HtmlRenderer
from core.analysis.behavioral_summary import get_behavioral_summary
from models.report_data import ReportData
from models.device_info import DeviceInfo
from models.timeline_event import TimelineEvent
from utils.logger import get_logger

log = get_logger(__name__)

_TOOL_VERSION = "1.0.0"
ReportFormat = Literal["html", "json"]


# (ReportData class was moved to models.report_data.py to resolve circular imports)
_IST = timezone(timedelta(hours=5, minutes=30))


# ─────────────────────────────────────────────────────────────────────────────
# ReportGenerator
# ─────────────────────────────────────────────────────────────────────────────

class ReportGenerator:
    """
    Builds and writes a forensic analysis report.

    Args:
        device:          Connected device's identity information.
        collection_time: UTC timestamp when artifacts were collected.
        output_dir:      Directory to write the report file into.
    """

    def __init__(
        self,
        device: DeviceInfo,
        collection_time: datetime,
        output_dir: Optional[Path] = None,
    ) -> None:
        self._device = device
        self._collection_time = collection_time
        self._output_dir = output_dir or REPORT_OUTPUT_DIR
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        timeline: list[TimelineEvent],
        fmt: ReportFormat = "html",
        stats: Optional[dict] = None,
        include_behavioral: bool = True,
    ) -> Path:
        """
        Assemble forensic findings and write the report to disk.

        Args:
            timeline: Fully processed TimelineEvent list (post-inference).
            fmt:      Output format — "html" or "json".
            stats:    Optional dict of engine statistics from normalization,
                      correlation, and inference reports.

        Returns:
            Path to the written report file.
        """
        log.info("Generating %s report for %s …", fmt.upper(), self._device.serial)
        report_data = self._build_report_data(timeline, stats or {}, include_behavioral)

        timestamp_str = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_serial = self._device.serial.replace(":", "_").replace("-", "_")

        if fmt == "html":
            filename = f"droidtrace_{safe_serial}_{timestamp_str}.html"
            output_path = self._output_dir / filename
            renderer = HtmlRenderer()
            renderer.render(report_data, output_path)
        elif fmt == "json":
            filename = f"droidtrace_{safe_serial}_{timestamp_str}.json"
            output_path = self._output_dir / filename
            self._write_json(report_data, output_path)
        else:
            raise ValueError(f"Unsupported report format: '{fmt}'")

        log.info("Report written to: %s", output_path)
        return output_path

    # ── Internal ───────────────────────────────────────────────────────────────

    def _build_report_data(
        self,
        timeline: list[TimelineEvent],
        stats: dict,
        include_behavioral: bool = True,
    ) -> ReportData:
        """Derive all computed views from the raw timeline."""
        flagged    = [e for e in timeline if e.flags]
        inferred   = [e for e in timeline if e.evidence_type == "INFERRED"]
        correlated = [e for e in timeline if e.evidence_type == "CORRELATED"]
        
        # ── Noise Filtering ──
        # Hide APP_LISTED events from the final timeline as requested.
        # They remain in the data structure but are hidden from the main view list.
        denoised_timeline = [e for e in timeline if e.event_type != "APP_LISTED"]

        # Flag frequency summary
        flag_summary: dict[str, int] = {}
        for event in flagged:
            for f in event.flags:
                flag_summary[f] = flag_summary.get(f, 0) + 1

        # Source frequency summary
        source_summary: dict[str, int] = {}
        for event in timeline:
            source_summary[event.source] = source_summary.get(event.source, 0) + 1

        # ── Suspicious Apps Summary ──
        # Ensure we use the exact same SAFE_PREFIXES as the Normalizer for consistency.
        suspicious_apps = sorted({
            e.app for e in flagged 
            if e.app and e.app != "system" 
            and not any(e.app.startswith(p) for p in SAFE_PREFIXES)
        })
        
        # Add special SUSPICIOUS flag for any app that was flagged
        # (Fusion rules already do this, but this is a safety check for DIRECT flags)
        for app in suspicious_apps:
            for event in timeline:
                if event.app == app and event.flags:
                    event.add_flag("SUSPICIOUS")

        return ReportData(
            device=self._device,
            collection_time=self._collection_time.astimezone(_IST),
            report_time=datetime.now(tz=_IST),
            tool_version=_TOOL_VERSION,
            timeline=denoised_timeline,
            flagged_events=flagged,
            inferred_events=inferred,
            correlated_events=correlated,
            suspicious_apps=suspicious_apps,
            flag_summary=flag_summary,
            source_summary=source_summary,
            behavioral_summary=get_behavioral_summary(timeline) if include_behavioral else None,
            stats=stats,
        )

    @staticmethod
    def _write_json(data: ReportData, path: Path) -> None:
        """Write a machine-readable JSON report."""
        def _serialize(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            if hasattr(obj, "__dict__"):
                return obj.__dict__
            return str(obj)

        payload = {
            "meta": {
                "tool":            f"DroidTrace Pro v{data.tool_version}",
                "report_time":     data.report_time.isoformat(),
                "collection_time": data.collection_time.isoformat(),
                "device":          data.device.__dict__,
            },
            "summary": {
                "total_events":      len(data.timeline),
                "flagged_events":    len(data.flagged_events),
                "inferred_events":   len(data.inferred_events),
                "correlated_events": len(data.correlated_events),
                "suspicious_apps":   data.suspicious_apps,
                "flag_summary":      data.flag_summary,
                "source_summary":    data.source_summary,
            },
            "timeline": [e.to_dict() for e in data.timeline],
            "flagged":  [e.to_dict() for e in data.flagged_events],
            "inferred": [e.to_dict() for e in data.inferred_events],
            "stats":    data.stats,
        }

        with path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=_serialize)
        log.debug("JSON report: %d bytes → %s", path.stat().st_size, path)
