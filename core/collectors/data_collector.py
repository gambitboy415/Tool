"""
core/collectors/data_collector.py
==================================
Data collection layer for DroidTrace Pro.

Responsibilities:
  - Pull raw forensic artifacts from a connected Android device via ADB
  - Each ``collect_*`` method maps to exactly one artifact source
  - Returns :class:`RawArtifact` objects — zero parsing happens here
  - Handles large ADB outputs safely (size cap, line streaming guard)
  - Optional / partial collection: failures in one source never abort others

Supported sources:
  1. Usage Stats     — ``dumpsys usagestats``        (app foreground/bg events)
  2. Installed Apps  — ``pm list packages -f``        (package → APK path map)
  3. Package Details — ``dumpsys package <pkg>``      (install time, perms, etc.)
  4. Screen State    — ``dumpsys power``              (screen on/off, wake locks)

Design:
  - All collected via :class:`~core.adb.AdbConnector` for consistent retry/timeout.
  - ``collect_all()`` runs all enabled collectors and aggregates results.
  - A ``CollectionResult`` wrapper is returned so callers can inspect per-source
    success/failure without raising exceptions for partial failures.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from config.settings import ADB_COMMAND_TIMEOUT, MAX_OUTPUT_BYTES
from core.adb.adb_connector import AdbConnector, AdbError, CommandTimeoutError
from models.raw_artifact import ArtifactType, RawArtifact
from utils.logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Result container
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CollectionResult:
    """
    Aggregated result from one ``DataCollector.collect_*`` call or
    from ``collect_all()``.

    Attributes:
        artifacts:    Successfully collected :class:`RawArtifact` objects.
        errors:       Map of ``ArtifactType → error message`` for failures.
        elapsed_ms:   Total wall-clock time for the collection run.
    """
    artifacts: list[RawArtifact] = field(default_factory=list)
    errors: dict[ArtifactType, str] = field(default_factory=dict)
    elapsed_ms: int = 0

    @property
    def success_count(self) -> int:
        return len(self.artifacts)

    @property
    def failure_count(self) -> int:
        return len(self.errors)

    @property
    def is_complete(self) -> bool:
        """True only if zero sources failed."""
        return not self.errors

    def summary(self) -> str:
        return (
            f"CollectionResult: {self.success_count} OK, "
            f"{self.failure_count} failed, {self.elapsed_ms}ms elapsed"
        )


# ─────────────────────────────────────────────────────────────────────────────
# DataCollector
# ─────────────────────────────────────────────────────────────────────────────

class DataCollector:
    """
    Orchestrates artifact collection from a connected Android device.

    Args:
        connector:       An already-connected :class:`AdbConnector` instance.
        max_workers:     Thread pool size for parallel collection (default 4).
        include_screen:  Whether to collect screen/power state (optional source).

    Example:
        connector = AdbConnector()
        connector.connect()

        collector = DataCollector(connector)
        result = collector.collect_all()
        for artifact in result.artifacts:
            print(artifact)
    """

    def __init__(
        self,
        connector: AdbConnector,
        max_workers: int = 4,
    ) -> None:
        self._connector = connector
        self._max_workers = max_workers

    # ── Public: individual sources ────────────────────────────────────────────

    def collect_usage_stats(self) -> RawArtifact:
        """
        Collect application usage statistics via ``dumpsys usagestats``.

        Forensic value:
          - Reveals which apps were in the foreground and when.
          - Exposes hidden app usage (e.g. an app labelled "Calculator" that
            ran at 03:00 for 45 minutes).
          - Contains package-level last-used timestamps even for deleted apps.

        Output size note:
          This command can produce very large output on active devices (>1 MB).
          We apply a byte-cap via ``_safe_shell`` to prevent memory issues.
        """
        log.info("Collecting usage stats …")
        return self._safe_shell(
            command="dumpsys usagestats",
            artifact_type=ArtifactType.USAGE_STATS,
            description="Application foreground/background usage events",
        )

    def collect_installed_packages(self) -> RawArtifact:
        """
        List all installed packages including their APK paths and UIDs.
        Command: ``pm list packages -f -U``

        Flags:
          -f  Include the path to the APK (enables path-based camouflage detection)
          -U  Include the app's UID (essential for network data attribution)

        We also request uninstalled packages (``-u``) separately in
        ``collect_uninstalled_packages()``; keeping them separate preserves
        the forensic distinction between active and historical installs.

        Output format:
          package:<apk_path>=<package_name> uid:<uid>
        """
        log.info("Collecting installed package list …")
        return self._safe_shell(
            command="pm list packages -f -U --user 0",
            artifact_type=ArtifactType.APP_LIST,
            description="Installed packages with APK paths and UIDs",
        )

    def collect_uninstalled_packages(self) -> RawArtifact:
        """
        List packages that have been uninstalled but still have data retained.
        Command: ``pm list packages -u -U``

        Forensic value:
          - Reveals apps that were deleted, potentially to hide evidence.
          - Combined with usage stats, can establish a timeline of deletion.
          - UID extraction allows linking historical network usage to deleted apps.
        """
        log.info("Collecting uninstalled/retained package list …")
        return self._safe_shell(
            command="pm list packages -u -U --user 0",
            artifact_type=ArtifactType.APP_LIST,
            description="Uninstalled packages with retained data and UIDs",
            metadata={"scope": "uninstalled"},
        )

    def collect_package_detail(self, package_name: str) -> RawArtifact:
        """
        Pull full package metadata for a single package.
        Command: ``dumpsys package <package_name>``

        Forensic value:
          - firstInstallTime / lastUpdateTime — crucial for timeline anchors
          - requestedPermissions — reveals capability fingerprint
          - versionName / versionCode — identifies trojanised/modified APKs
          - signatures — cryptographic identity of the APK signer

        Args:
            package_name:  Package identifier, e.g. ``com.whatsapp``.

        Returns:
            :class:`RawArtifact` with ``metadata["package"]`` set.
        """
        # Basic package name validation to prevent shell injection
        _validate_package_name(package_name)
        log.info("Collecting package detail: %s", package_name)
        return self._safe_shell(
            command=f"dumpsys package {package_name}",
            artifact_type=ArtifactType.APP_DETAIL,
            description=f"Package detail: {package_name}",
            metadata={"package": package_name},
        )

    def collect_network_stats(self) -> RawArtifact:
        """
        Collect detailed per-UID network usage via ``dumpsys netstats``.

        Forensic value:
          - Reveals exactly how many bytes each application transferred.
          - Distinguishes between foreground and background data usage.
          - Essential for detecting data exfiltration windows.

        Implementation note:
          We use the ``--uid`` and ``--full`` flags to ensure we get a historical
          breakdown of usage rather than just current active interface counters.
        """
        log.info("Collecting per-app network usage statistics …")
        return self._safe_shell(
            command="dumpsys netstats --uid --full",
            artifact_type=ArtifactType.NETWORK,
            description="Per-app historical network usage statistics",
        )

    def collect_all_package_details(
        self,
        packages: Optional[list[str]] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> list[RawArtifact]:
        """
        Collect ``dumpsys package`` output for multiple packages in parallel.

        If ``packages`` is None, this method first runs ``collect_installed_packages()``
        and parses the package names automatically.

        Args:
            packages:          Explicit list of package names. Auto-detected if None.
            progress_callback: Optional ``callback(done, total)`` for UI progress bars.

        Returns:
            List of :class:`RawArtifact`, one per package.
        """
        if packages is None:
            raw_inst = self.collect_installed_packages()
            raw_uninst = self.collect_uninstalled_packages()
            
            pkg_inst = _parse_package_names(raw_inst.raw_output)
            pkg_uninst = _parse_package_names(raw_uninst.raw_output)
            
            packages = sorted(set(pkg_inst + pkg_uninst))
            log.info("Auto-detected %d packages (including uninstalled) for detail collection", len(packages))

        total = len(packages)
        results: list[RawArtifact] = []
        done_count = 0

        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {
                pool.submit(self.collect_package_detail, pkg): pkg
                for pkg in packages
            }
            for future in as_completed(futures):
                pkg = futures[future]
                try:
                    artifact = future.result()
                    results.append(artifact)
                except AdbError as exc:
                    log.warning("Failed to collect detail for %s: %s", pkg, exc)
                    # Still append a failed artifact so parsers know it was attempted
                    results.append(
                        RawArtifact(
                            artifact_type=ArtifactType.APP_DETAIL,
                            source_command=f"dumpsys package {pkg}",
                            raw_output="",
                            collected_at=datetime.now(tz=timezone.utc),
                            device_serial="",
                            error=str(exc),
                            metadata={"package": pkg},
                        )
                    )
                finally:
                    done_count += 1
                    if progress_callback:
                        progress_callback(done_count, total)

        log.info(
            "Package detail collection complete: %d/%d succeeded",
            sum(1 for r in results if r.is_successful), total,
        )
        return results

    def collect_screen_state(self) -> RawArtifact:
        """
        Collect power manager state via ``dumpsys power``.

        Forensic value:
          - Current screen state (ON / OFF / DOZE)
          - Wake lock holders — reveals background processes keeping screen awake
          - mWakefulness and mScreenState fields are key timeline anchors
          - Last wakefulness change timestamp

        Note:
          This is an optional artifact — many forensic scenarios don't require it.
          Controlled by ``include_screen`` constructor param.
        """
        log.info("Collecting screen/power state …")
        return self._safe_shell(
            command="dumpsys power",
            artifact_type=ArtifactType.POWER,
            description="Screen and wake-lock state from power manager",
        )

    def collect_all(
        self,
        options: dict,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> CollectionResult:
        """
        Run requested collectors and return an aggregated :class:`CollectionResult`.

        Args:
            options:           Dict of artifact keys (True/False) and analysis flags.
                               keys: usage_stats, installed_apps, uninstalled_apps,
                                     network_stats, package_details, screen_state.
            progress_callback: Optional ``callback(done, total)`` for progress.
        """
        import time
        t_start = time.monotonic()
        result = CollectionResult()

        # Build the list of (name, internal_fn) tasks to run
        # Map UI keys to collector methods
        _MANIFEST = [
            ("usage_stats",           "usage_stats",           self.collect_usage_stats),
            ("installed_packages",    "installed_apps",        self.collect_installed_packages),
            ("uninstalled_packages",  "uninstalled_apps",      self.collect_uninstalled_packages),
            ("network_stats",         "network_stats",         self.collect_network_stats),
            ("screen_state",          "screen_state",          self.collect_screen_state),
        ]

        tasks = []
        for name, option_key, fn in _MANIFEST:
            if options.get(option_key, True):
                tasks.append((name, fn))

        total = len(tasks)
        done = 0

        # Run core tasks in parallel
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {
                pool.submit(fn): (name, fn)
                for name, fn in tasks
            }
            for future in as_completed(futures):
                name, _ = futures[future]
                try:
                    artifact_or_list = future.result()
                    if isinstance(artifact_or_list, list):
                        result.artifacts.extend(artifact_or_list)
                    else:
                        result.artifacts.append(artifact_or_list)
                    log.debug("Collector '%s' succeeded", name)
                except AdbError as exc:
                    log.error("Collector '%s' failed: %s", name, exc)
                    # Map the name to an approximate artifact type for the error dict
                    atype = _name_to_artifact_type(name)
                    result.errors[atype] = str(exc)
                finally:
                    done += 1
                    if progress_callback:
                        progress_callback(done, total)

        # Stage 5: Package Details (Parallelized internal to method)
        if options.get("package_details", False):
            log.info("Starting detailed package analysis (Stage 5) …")
            pkg_artifacts = self.collect_all_package_details(
                progress_callback=progress_callback
            )
            result.artifacts.extend(pkg_artifacts)

        result.elapsed_ms = int((time.monotonic() - t_start) * 1000)
        log.info(result.summary())
        return result

    # ── Internal helper ───────────────────────────────────────────────────────

    def _safe_shell(
        self,
        command: str,
        artifact_type: ArtifactType,
        description: str,
        metadata: Optional[dict] = None,
    ) -> RawArtifact:
        """
        Execute an ADB shell command and return a :class:`RawArtifact`.

        Safety measures:
          - Catches all :class:`AdbError` subclasses and attaches to artifact
            instead of propagating; caller decides what to do with failures.
          - Enforces ``MAX_OUTPUT_BYTES`` to prevent OOM on unexpectedly large output.
          - Records the exact command string for forensic chain-of-custody.

        Args:
            command:       Shell command to run (no ``adb shell`` prefix).
            artifact_type: Category for the resulting artifact.
            description:   Human-readable label (stored in metadata).
            metadata:      Additional key-value context to attach.

        Returns:
            :class:`RawArtifact` — always; ``error`` field set on failure.
        """
        collected_at = datetime.now(tz=timezone.utc)
        full_metadata = {"description": description, **(metadata or {})}
        error: Optional[str] = None
        raw_output = ""

        try:
            raw_output = self._connector.shell(command)
            # Enforce byte cap: very large outputs are truncated with a marker
            if len(raw_output.encode("utf-8", errors="replace")) > MAX_OUTPUT_BYTES:
                original_lines = raw_output.count("\n")
                raw_output = raw_output[: MAX_OUTPUT_BYTES].rsplit("\n", 1)[0]
                truncated_lines = raw_output.count("\n")
                marker = (
                    f"\n[DROIDTRACE: OUTPUT TRUNCATED — "
                    f"{original_lines} lines, showing {truncated_lines}]"
                )
                raw_output += marker
                log.warning(
                    "Output for '%s' truncated at %d bytes (%d → %d lines)",
                    command, MAX_OUTPUT_BYTES, original_lines, truncated_lines,
                )

            # Diagnostic: Log if output is empty (potential permission or format issue)
            if not raw_output.strip():
                log.warning("Empty output received for command: '%s'", command)
            else:
                log.debug("Collected %d chars for command: '%s'", len(raw_output), command)

        except CommandTimeoutError as exc:
            error = f"TIMEOUT: {exc}"
            log.error("Timeout collecting '%s': %s", command, exc)
        except AdbError as exc:
            error = str(exc)
            log.error("ADB error collecting '%s': %s", command, exc)

        # Attempt to read the device serial safely
        try:
            device_serial = self._connector._serial or "unknown"
        except AttributeError:
            device_serial = "unknown"

        return RawArtifact(
            artifact_type=artifact_type,
            source_command=f"adb shell {command}",
            raw_output=raw_output,
            collected_at=collected_at,
            device_serial=device_serial,
            error=error,
            metadata=full_metadata,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers (pure functions, no IO)
# ─────────────────────────────────────────────────────────────────────────────

def _validate_package_name(package_name: str) -> None:
    """
    Validate that a package name contains only safe characters.
    Prevents shell injection via crafted package name strings.

    Valid pattern: alphanumeric, dots, underscores, and hyphens only.

    Raises:
        ValueError: If the package name contains suspicious characters.
    """
    if not re.fullmatch(r"[a-zA-Z0-9._\-]+", package_name):
        raise ValueError(
            f"Invalid package name '{package_name}'. "
            "Only alphanumeric characters, dots, underscores, and hyphens are allowed."
        )


def _parse_package_names(pm_output: str) -> list[str]:
    """
    Extract package names from ``pm list packages -f`` output.

    Input line format:
        package:/data/app/~~abc/com.example.app-1/base.apk=com.example.app
        OR with -U:
        package:/data/app/~~abc/com.example.app-1/base.apk=com.example.app uid:10001

    Returns:
        Sorted, deduplicated list of package name strings.
    """
    packages: list[str] = []
    for line in pm_output.splitlines():
        line = line.strip()
        if not line.startswith("package:"):
            continue
        # The package name is after the last '='
        if "=" in line:
            # Handle potential metadata like "uid:XXXX" after the package name
            pkg_with_metadata = line.rsplit("=", 1)[-1].strip()
            pkg = pkg_with_metadata.split()[0]
            if pkg:
                packages.append(pkg)
    return sorted(set(packages))


def _name_to_artifact_type(name: str) -> ArtifactType:
    """Map a collector task name string to an ArtifactType for error reporting."""
    _MAP = {
        "usage_stats":          ArtifactType.USAGE_STATS,
        "installed_packages":   ArtifactType.APP_LIST,
        "uninstalled_packages": ArtifactType.APP_LIST,
        "screen_state":         ArtifactType.POWER,
        "package_details":      ArtifactType.APP_DETAIL,
        "network_stats":        ArtifactType.NETWORK,
    }
    return _MAP.get(name, ArtifactType.UNKNOWN)
