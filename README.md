# 📱 DroidTrace Pro — Behavioral Forensic Reconstruction Engine

**DroidTrace Pro** is a research-grade Android forensic tool designed to reconstruct human-activity timelines from raw device artifacts. Unlike simple log viewers, it employs a deterministic multi-stage pipeline to normalize, correlate, and infer behavioral patterns without relying on root access or probabilistic scoring.

---

## 🚀 Key Features

*   **Forensic Timeline Reconstruction**: Unified, chronologically strict timeline derived from multiple sources (UsageStats, Logcat, Package Manager).
*   **Behavioral Intelligence**: 12+ deterministic inference rules (e.g., `ANTI_FORENSIC_SEQUENCE`, `DATA_EXFILTRATION_WINDOW`, `APP_CAMOUFLAGE`).
*   **Session Reconstruction**: Aggregates raw foreground/background markers into logical user sessions with duration and frequency analysis.
*   **Source Authority Normalization**: Handles conflicting timestamps by prioritizing authoritative forensic sources (Package Detail > Usage Stats > Inferred).
*   **Interactive Dashboard**: PyQt6-based analyst interface with deep-dive event inspection, behavioral flag filters, and forensic report generation.
*   **Standalone Portability**: Fully self-contained, including embedded ADB binaries for zero-install field use.

---

## 🛠️ System Architecture

DroidTrace Pro follows a strictly decoupled forensic pipeline:

1.  **DataCollector**: Orchestrates ADB to extract raw raw logcat and usagestats artifacts.
2.  **Parser**: Converts raw output into structured `ParsedEvent` objects.
3.  **Normalizer**: Validates timestamps, applies forensic bounds (2015-2035), and handles clock skew.
4.  **TimelineBuilder**: Builds the linear sequence and assigns stable indices.
5.  **SessionEngine**: Synthesizes session events from app lifecycle markers.
6.  **CorrelationEngine**: Links related events (e.g., Network Toggle → App Activity).
7.  **InferenceEngine**: Applies behavioral rules to flag suspicious patterns or synthesize high-level forensic findings.
8.  **UI**: Professional analyst dashboard for review and reporting.

---

## 📦 Installation

### Prerequisites
*   **Python**: 3.12+ (Recommended)
*   **ADB**: Included in `assets/adb/` (No system install required)

### Setup
1.  **Clone the repository**:
    ```bash
    git clone https://github.com/gambitboy415/Tool.git
    cd Tool
    ```
2.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

---

## 💻 Usage

### Launch the UI
Run the main entry point to start the forensic dashboard:
```bash
python main.py
```

### Forensic Analysis Workflow
1.  **Connect Device**: Ensure USB Debugging is enabled on the target Android device.
2.  **Acquire Artifacts**: Click "Connect Device" and "Start Scan" to begin historical data extraction.
3.  **Review Timeline**: Use the **Event View** to analyze the chronologically strict activity list.
4.  **Inspect Behavioral Flags**: Filter for **Flagged** or **Suspicious** events to find anti-forensic indicators or anomalies.
5.  **Generate Report**: Click "Export Report" to generate an audit-ready PDF/HTML summary of findings.

---

## ⚖️ Forensic Integrity

*   **Non-Destructive**: DroidTrace Pro never alters source evidence. It creates flagged "NORMALIZED" copies for analysis.
*   **Deterministic Rules**: Every inference is based on explicit, auditable logic defined in `core/inference/rules/`.
*   **Audit Trail**: Every event maintains a `source_command` and `raw_fields` reference back to its original artifact source.

---

## 📄 License & Disclaimer

**For Forensic Research Use Only.**
DroidTrace Pro is designed for authorized digital forensic investigations. Unauthorized use against devices for which you do not have forensic authorization may be illegal.

Copyright © 2026 DroidTrace Pro Contributors.
