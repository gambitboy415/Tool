import json
from datetime import datetime, timezone, timedelta
from core.analytics.behavior_engine import BehaviorEngine
from core.reporting.report_generator import ReportGenerator
from models.timeline_event import TimelineEvent
from models.device_info import DeviceInfo

def generate_mock_timeline():
    now = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    
    return [
        # 1. Instagram Install
        TimelineEvent(
            timestamp=now - timedelta(days=1),
            app="com.instagram.android",
            event_type="APP_INSTALLED",
            description="Installed from Play Store"
        ),
        # 2. Instagram Session (Short)
        TimelineEvent(
            timestamp=now - timedelta(hours=20),
            app="com.instagram.android",
            event_type="APP_SESSION",
            raw_fields={"duration_sec": 300},
            description="User scrolled feed"
        ),
        # 3. Malicious App Install
        TimelineEvent(
            timestamp=now - timedelta(hours=10),
            app="com.suspicious.cleaner",
            event_type="APP_INSTALLED",
            raw_fields={"apk_location": "user"},
            description="Sideloaded APK"
        ),
        # 4. Malicious App Session (Anomaly: > 2 hours)
        TimelineEvent(
            timestamp=now - timedelta(hours=5),
            app="com.suspicious.cleaner",
            event_type="APP_SESSION",
            raw_fields={"duration_sec": 7500}, # 2.08 hours
            description="Background data processing"
        ),
        # 5. WhatsApp Night Activity (Anomaly: 02:00)
        TimelineEvent(
            timestamp=datetime(2024, 1, 15, 2, 30, 0, tzinfo=timezone.utc),
            app="com.whatsapp",
            event_type="APP_OPENED",
            description="Message sent"
        ),
        # 6. System App (Google Search)
        TimelineEvent(
            timestamp=now - timedelta(minutes=30),
            app="com.google.android.googlequicksearchbox",
            event_type="APP_OPENED",
            description="Search query: 'how to delete forensic artifacts'"
        )
    ]

def verify_engine():
    now = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    timeline = generate_mock_timeline()
    # Pre-index and set ISO for mock events
    for i, e in enumerate(timeline):
        e.sequence_index = i
        e.iso_timestamp = e.timestamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if e.timestamp else "UNKNOWN"
        e.valid_time = True

    engine = BehaviorEngine(timeline)

    print("=" * 48)
    print("DROIDTRACE PRO - BEHAVIOR ANALYTICS VERIFICATION")
    print("=" * 48)

    # Phase 2: App Profiles
    print("\n[PHASE 2] APP PROFILES:")
    profiles = engine.app_profiles()
    for app, stats in profiles.items():
        print(f"  * {app}: {stats['sessions']} sessions, {stats['total_time']/60:.1f}m used")
        print(f"    Last: {stats['last_used']} | Installed: {stats['installed']}")

    # Phase 3: Heatmap
    print("\n[PHASE 3] USAGE HEATMAP (Top Hours):")
    heatmap = engine.usage_heatmap()
    for h, count in sorted(heatmap.items(), key=lambda x: x[1], reverse=True)[:5]:
        if count > 0:
            print(f"  * {h:02d}:00 -> {count} events")

    # Phase 4: Risk Classification
    print("\n[PHASE 4] RISK CLASSIFICATION:")
    for app in ["com.google.android.gms", "com.whatsapp", "com.suspicious.cleaner"]:
        print(f"  * {app:<30} -> {engine.classify_risk(app)}")

    # Phase 5: App Lifecycle
    print("\n[PHASE 5] APP LIFECYCLE (Instagram):")
    lifecycle = engine.app_lifecycle("com.instagram.android")
    for step in lifecycle:
        print(f"  * {step['timestamp']} | {step['event_type']} | {step['description']}")

    # Phase 6: Device Summary
    print("\n[PHASE 6] DEVICE SUMMARY:")
    summary = engine.device_summary()
    print(f"  * Total Sessions:   {summary['total_sessions']}")
    print(f"  * Total Active Time: {summary['total_active_time']/3600:.2f} hours")
    print(f"  * Top App:          {summary['most_used_app']}")

    # Phase 7: System Filter
    print("\n[PHASE 7] SYSTEM APP FILTER:")
    user_apps = engine.filter_user_apps()
    print(f"  * Events remaining after filtering system: {len(user_apps)} of {len(timeline)}")

    # Phase 8: Anomaly Detection
    print("\n[PHASE 8] ANOMALY DETECTION:")
    anomalies = engine.detect_anomalies()
    for entry in anomalies:
        print(f"  * {entry}")

    # Phase 9: Search
    print("\n[PHASE 9] SEARCH (Query: 'cleaner'):")
    results = engine.search("cleaner")
    for r in results:
        print(f"  * {r.timestamp} | {r.app} | {r.description}")

    # Phase 12: Report Generation Integration Test
    print("\n[PHASE 12] REPORT GENERATION TEST:")
    device = DeviceInfo(
        serial="TEST_SERIAL",
        manufacturer="Google",
        model="Pixel 7",
        android_version="13",
        sdk_version=33,
        build_fingerprint="google/cheetah/cheetah:13/TQ3A.230705.001/10210210:user/release-keys",
        connected_at=now
    )
    generator = ReportGenerator(device, now)
    
    # Generate report
    report_path = generator.generate(timeline, fmt="html")
    print(f"  * HTML Report generated: {report_path.name}")
    
    # Verify content
    content = report_path.read_text(encoding="utf-8")
    if "Advanced Behavior Analytics Dashboard" in content:
        print("  * Verification: Dashboard section FOUND in HTML.")
    else:
        print("  * ERROR: Dashboard section MISSING from HTML.")

    if "heatmap-bar" in content:
        print("  * Verification: Heatmap bars FOUND in HTML.")
    
    if "com.suspicious.cleaner" in content and "Long Session" in content:
        print("  * Verification: Anomalies FOUND in HTML.")

    print("\n" + "=" * 48)
    print("VERIFICATION COMPLETE")
    print("=" * 48)

if __name__ == "__main__":
    verify_engine()
