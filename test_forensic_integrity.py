import os
from datetime import datetime, timezone, timedelta
from core.timeline.normalizer import normalize_events, NormalizationConfig
from core.timeline.timeline_builder import build_timeline
from core.inference.inference_engine import InferenceEngine
from models.parsed_event import ParsedEvent
from utils.logger import get_logger

log = get_logger(__name__)

def test_forensic_integrity():
    collection_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    
    # ── Test Scenarios ──
    # 1. Valid timestamp (2024)
    # 2. Out-of-bounds (1970) -> Should be INVALIDATED
    # 3. Out-of-bounds (2099) -> Should be INVALIDATED
    # 4. None timestamp -> Should be INVALIDATED
    # 5. Skewed timestamp (ahead of collection) -> Should be CEILED or FLAGGED
    
    raw_events = [
        ParsedEvent(
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
            app="com.whatsapp",
            event_type="APP_OPENED",
            source="usage_stats"
        ),
        ParsedEvent(
            timestamp=datetime(1970, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            app="com.malicious.tool",
            event_type="APP_INSTALLED",
            source="package_detail"
        ),
        ParsedEvent(
            timestamp=datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
            app="com.malicious.tool",
            event_type="APP_OPENED",
            source="usage_stats"
        ),
        ParsedEvent(
            timestamp=None,
            app="com.malicious.tool",
            event_type="APP_OPENED",
            source="usage_stats"
        ),
        ParsedEvent(
            timestamp=datetime(2024, 1, 15, 12, 5, 0, tzinfo=timezone.utc), # 5 mins ahead
            app="com.whatsapp",
            event_type="APP_CLOSED",
            source="usage_stats"
        )
    ]
    
    # 1. Normalization
    log.info("Starting Normalization Test...")
    normalized, report = normalize_events(raw_events, collection_time)
    print("\nNormalization Report:")
    print(report.summary())
    
    assert report.timestamp_invalidated == 3, f"Expected 3 invalidated, got {report.timestamp_invalidated}"
    
    # 2. Timeline Building
    log.info("Starting Timeline Building Test...")
    timeline = build_timeline(normalized)
    
    # 3. Inference Engine (should trigger TimestampIntegrityRule for com.malicious.tool)
    log.info("Starting Inference Engine Test...")
    engine = InferenceEngine()
    inference_report = engine.run(timeline)
    print("\nInference Report:")
    print(inference_report.summary())
    
    # 4. Verify Results
    print("\nFinal Timeline Events:")
    for e in timeline:
        print(f"[{e.iso_timestamp}] {e.app:<20} | {e.event_type:<20} | Valid: {e.valid_time} | Flags: {e.flags}")
        
    # Check if com.malicious.tool was flagged for integrity issues
    malicious_events = [e for e in timeline if e.app == "com.malicious.tool"]
    integrity_flagged = any("TEMPORAL_INTEGRITY_INVALID" in e.flags for e in malicious_events)
    assert integrity_flagged, "com.malicious.tool should have been flagged for Timestamp Integrity (TEMPORAL_INTEGRITY_INVALID)"

    print("\nVerification SUCCESS: All forensic integrity cases handled correctly.")

if __name__ == "__main__":
    test_forensic_integrity()
