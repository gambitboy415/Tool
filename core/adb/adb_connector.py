"""
core/adb/adb_connector.py
==========================
Production-ready ADB communication layer for DroidTrace Pro.

Responsibilities:
  - Locate the ADB executable (bundled asset or system PATH)
  - Detect and enumerate connected Android devices
  - Execute arbitrary ADB shell commands with timeout + retry
  - Return clean, decoded output; never leak raw subprocess internals
  - Emit structured forensic log entries for every operation

Design notes:
  - All public methods raise specific, typed exceptions (see bottom of file)
    so callers can handle errors deterministically — no bare `except Exception`.
  - Retry logic uses truncated exponential back-off to avoid hammering USB.
  - The class is intentionally NOT a singleton; one instance per session
    is the expected usage pattern (dependency-injection friendly).
  - Thread-safe: subprocess calls are stateless; the selected device serial
    is stored per-instance so multiple connectors can target different devices.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config.settings import (
    ADB_BUNDLED_PATH,
    ADB_COMMAND_TIMEOUT,
    ADB_DEVICE_WAIT_TIMEOUT,
    ADB_MAX_RETRIES,
    ADB_RETRY_BACKOFF_BASE,
)
from models.device_info import DeviceInfo
from utils.logger import get_logger

log = get_logger(__name__)

# Windows subprocess flag to suppress terminal popups
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


# ─────────────────────────────────────────────────────────────────────────────
# Custom Exceptions
# ─────────────────────────────────────────────────────────────────────────────

class AdbError(RuntimeError):
    """Base class for all ADB-related errors."""


class AdbNotFoundError(AdbError):
    """Raised when the adb executable cannot be located."""


class NoDeviceError(AdbError):
    """Raised when no Android device is detected by ADB."""


class MultipleDevicesError(AdbError):
    """Raised when multiple devices are connected and no serial was specified."""


class CommandTimeoutError(AdbError):
    """Raised when an ADB command exceeds its timeout budget."""


class CommandFailedError(AdbError):
    """Raised when ADB returns a non-zero exit code after all retries."""


# ─────────────────────────────────────────────────────────────────────────────
# Internal result container
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AdbResult:
    """
    The clean, normalised output of a single ADB command execution.

    Attributes:
        stdout:      Decoded, stripped stdout from the command.
        stderr:      Decoded stderr (usually empty on success).
        exit_code:   Process exit code (0 = success).
        duration_ms: Wall-clock time spent executing the command.
        command:     The full command list that was executed.
    """
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    command: list[str]

    @property
    def is_success(self) -> bool:
        return self.exit_code == 0

    def __str__(self) -> str:
        preview = self.stdout[:80].replace("\n", "↵")
        return (
            f"AdbResult(exit={self.exit_code}, "
            f"duration={self.duration_ms}ms, stdout='{preview}…')"
        )


# ─────────────────────────────────────────────────────────────────────────────
# AdbConnector
# ─────────────────────────────────────────────────────────────────────────────

class AdbConnector:
    """
    Manages all communication with a single Android device via ADB.

    Typical lifecycle:
        connector = AdbConnector()
        connector.connect()                    # auto-selects device
        result = connector.shell("getprop ro.build.version.release")
        info = connector.get_device_info()
        connector.disconnect()

    Args:
        serial:         Target device serial. If None, auto-detected (raises
                        MultipleDevicesError if >1 device is present).
        adb_path:       Override the ADB binary path. Defaults to bundled
                        binary or PATH discovery.
        timeout:        Per-command timeout in seconds (overrides settings).
        max_retries:    Number of retry attempts for transient failures.
    """

    def __init__(
        self,
        serial: Optional[str] = None,
        adb_path: Optional[Path] = None,
        timeout: int = ADB_COMMAND_TIMEOUT,
        max_retries: int = ADB_MAX_RETRIES,
    ) -> None:
        self._serial: Optional[str] = serial
        self._timeout: int = timeout
        self._max_retries: int = max_retries
        self._adb_exe: Path = self._resolve_adb(adb_path)
        self._connected: bool = False
        log.debug("AdbConnector initialised (adb=%s, serial=%s)", self._adb_exe, serial)

    # ── Public API ────────────────────────────────────────────────────────────

    def connect(self) -> DeviceInfo:
        """
        Detect the target device, confirm ADB authorisation, and populate
        device metadata.  Must be called before any ``shell()`` commands.

        Returns:
            A populated :class:`DeviceInfo` instance.

        Raises:
            NoDeviceError: No authorised device found.
            MultipleDevicesError: Multiple devices and no serial specified.
        """
        log.info("Initiating device connection …")
        devices = self.list_devices()

        if not devices:
            log.error("No ADB devices found")
            raise NoDeviceError(
                "No Android device detected. Ensure USB debugging is enabled "
                "and the device is authorised."
            )

        if self._serial is None:
            if len(devices) > 1:
                serials = [d["serial"] for d in devices]
                raise MultipleDevicesError(
                    f"Multiple devices detected: {serials}. "
                    "Specify a serial to disambiguate."
                )
            self._serial = devices[0]["serial"]

        # Verify the chosen serial is actually in the list
        known_serials = {d["serial"] for d in devices}
        if self._serial not in known_serials:
            raise NoDeviceError(f"Device '{self._serial}' not found in: {known_serials}")

        self._connected = True
        info = self.get_device_info()
        log.info("Connected to %s", info)
        return info

    def disconnect(self) -> None:
        """
        Mark the connection as closed.  Does not physically disconnect USB —
        ADB does not require an explicit "close" for USB transports.
        """
        log.info("Disconnecting from device %s", self._serial)
        self._connected = False
        self._serial = None

    def list_devices(self) -> list[dict[str, str]]:
        """
        Return all devices currently visible to ADB.

        Returns:
            List of dicts with keys: ``serial``, ``state``, ``transport``.
            State is typically "device", "offline", or "unauthorized".
        """
        result = self._run_adb(["devices", "-l"], use_serial=False)
        devices = _parse_devices_output(result.stdout)
        log.info("Found %d device(s): %s", len(devices), [d["serial"] for d in devices])
        return devices

    def shell(
        self,
        command: str,
        timeout: Optional[int] = None,
        retries: Optional[int] = None,
    ) -> str:
        """
        Execute an ``adb shell`` command on the connected device.

        Args:
            command:  The shell command string (e.g. "getprop ro.product.model").
            timeout:  Override the instance-level timeout for this call only.
            retries:  Override the instance-level retry count for this call only.

        Returns:
            Decoded, stripped stdout output string.

        Raises:
            NoDeviceError:       No device connected.
            CommandTimeoutError: Command exceeded timeout after all retries.
            CommandFailedError:  Non-zero exit code after all retries.
        """
        self._require_connection()
        result = self._run_adb(
            ["shell", command],
            timeout=timeout,
            retries=retries,
        )
        return result.stdout

    def get_device_info(self) -> DeviceInfo:
        """
        Query the device for its identity properties and return a DeviceInfo.
        Uses individual ``getprop`` calls so partial failures are handled
        gracefully (a missing prop returns an empty string, not an exception).
        """
        self._require_connection()
        log.debug("Fetching device properties …")

        def prop(key: str, fallback: str = "unknown") -> str:
            try:
                return self.shell(f"getprop {key}").strip() or fallback
            except AdbError:
                return fallback

        sdk_raw = prop("ro.build.version.sdk", "0")
        sdk = int(sdk_raw) if sdk_raw.isdigit() else 0

        info = DeviceInfo(
            serial=self._serial,
            model=prop("ro.product.model"),
            manufacturer=prop("ro.product.manufacturer"),
            android_version=prop("ro.build.version.release"),
            sdk_version=sdk,
            build_fingerprint=prop("ro.build.fingerprint"),
            connected_at=datetime.now(tz=timezone.utc),
            transport_type=_infer_transport(self._serial),
        )
        log.debug("DeviceInfo: %s", info)
        return info

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _require_connection(self) -> None:
        """Guard: raise NoDeviceError if connect() has not been called."""
        if not self._connected or self._serial is None:
            raise NoDeviceError("No device connected. Call connect() first.")

    def _run_adb(
        self,
        args: list[str],
        use_serial: bool = True,
        timeout: Optional[int] = None,
        retries: Optional[int] = None,
    ) -> AdbResult:
        """
        Internal dispatcher: builds the full ADB command, handles retry loop,
        and returns an :class:`AdbResult`.

        Retry policy:
          - Retry only on timeout and on specific transient error strings.
          - Non-transient failures (e.g. "error: closed") are NOT retried.
          - Back-off = base ** attempt seconds (capped at 8s).

        Args:
            args:        ADB sub-command args (e.g. ["shell", "getprop ..."])
            use_serial:  Prepend -s <serial> to address specific device.
            timeout:     Seconds before TimeoutExpired (falls back to instance default).
            retries:     Max retry count (falls back to instance default).

        Returns:
            :class:`AdbResult` from the last successful (or final) attempt.
        """
        effective_timeout = timeout if timeout is not None else self._timeout
        effective_retries = retries if retries is not None else self._max_retries

        cmd: list[str] = [str(self._adb_exe)]
        if use_serial and self._serial:
            cmd += ["-s", self._serial]
        cmd += args

        last_error: Optional[Exception] = None

        for attempt in range(effective_retries + 1):
            if attempt > 0:
                backoff = min(ADB_RETRY_BACKOFF_BASE ** attempt, 8.0)
                log.warning(
                    "Retry %d/%d for command '%s' (back-off %.1fs) …",
                    attempt, effective_retries, " ".join(args[:3]), backoff,
                )
                time.sleep(backoff)

            t_start = time.monotonic()
            try:
                startupinfo = None
                if sys.platform == "win32":
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = subprocess.SW_HIDE

                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=effective_timeout,
                    text=False,          # handle encoding manually for safety
                    creationflags=_CREATE_NO_WINDOW,
                    startupinfo=startupinfo,
                )
                duration_ms = int((time.monotonic() - t_start) * 1000)
                stdout = _safe_decode(proc.stdout)
                stderr = _safe_decode(proc.stderr)

                result = AdbResult(
                    stdout=stdout.strip(),
                    stderr=stderr.strip(),
                    exit_code=proc.returncode,
                    duration_ms=duration_ms,
                    command=cmd,
                )
                log.debug(
                    "ADB %s → exit=%d, %dms, %d chars",
                    " ".join(args[:3]), result.exit_code, duration_ms, len(stdout),
                )

                # ADB can exit 0 but write errors to stderr
                if result.exit_code != 0 or _is_adb_error_output(stderr):
                    if not _is_transient_error(stderr):
                        # Non-transient: do not retry
                        raise CommandFailedError(
                            f"ADB command failed (exit {result.exit_code}): "
                            f"{' '.join(args)}\nstderr: {stderr[:200]}"
                        )
                    last_error = CommandFailedError(stderr[:200])
                    continue  # retry

                return result

            except subprocess.TimeoutExpired as exc:
                duration_ms = int((time.monotonic() - t_start) * 1000)
                log.warning(
                    "Command timed out after %ds (attempt %d): %s",
                    effective_timeout, attempt + 1, " ".join(args[:3]),
                )
                last_error = CommandTimeoutError(
                    f"Command '{' '.join(args)}' timed out after {effective_timeout}s"
                )
                continue  # retry on timeout

        # All retries exhausted
        raise last_error or CommandFailedError(
            f"Command '{' '.join(args)}' failed after {effective_retries} retries."
        )

    @staticmethod
    def _resolve_adb(override: Optional[Path]) -> Path:
        """
        Locate the ADB executable using the following priority order:
          1. Explicit override path (from constructor)
          2. Bundled binary at assets/adb/adb.exe
          3. System PATH (where the user has Platform Tools installed)

        Raises:
            AdbNotFoundError: If ADB cannot be found by any strategy.
        """
        if override is not None:
            if override.is_file():
                log.debug("Using explicit ADB override: %s", override)
                return override
            raise AdbNotFoundError(f"Specified ADB path does not exist: {override}")

        # Strategy 2: bundled binary
        if ADB_BUNDLED_PATH.is_file():
            log.debug("Using bundled ADB: %s", ADB_BUNDLED_PATH)
            return ADB_BUNDLED_PATH

        # Strategy 3: system PATH
        system_adb = shutil.which("adb")
        if system_adb:
            log.debug("Using system ADB from PATH: %s", system_adb)
            return Path(system_adb)

        raise AdbNotFoundError(
            "ADB executable not found. Install Android Platform Tools or "
            "place adb.exe in assets/adb/."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helper functions (pure, no side effects)
# ─────────────────────────────────────────────────────────────────────────────

def _safe_decode(raw: bytes) -> str:
    """
    Decode ADB output bytes tolerantly.
    ADB output is mostly ASCII but can contain device-locale text.
    We try UTF-8, fall back to latin-1 which never raises.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


def _parse_devices_output(output: str) -> list[dict[str, str]]:
    """
    Parse the output of ``adb devices -l`` into a list of device dicts.

    Example input lines:
        emulator-5554          device product:sdk_gphone64_x86_64 ...
        R5CW3XXXXX             unauthorized
        192.168.1.5:5555       offline
    """
    devices = []
    for line in output.splitlines():
        line = line.strip()
        # Skip header and blank lines
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        devices.append({
            "serial": serial,
            "state": state,
            "transport": _infer_transport(serial),
        })
    return devices


def _infer_transport(serial: Optional[str]) -> str:
    """
    Infer the transport type from the device serial string.
      - "emulator-XXXX"  → "emulator"
      - "n.n.n.n:port"   → "tcp"
      - anything else    → "usb"
    """
    if serial is None:
        return "unknown"
    if serial.startswith("emulator-"):
        return "emulator"
    if re.match(r"^\d+\.\d+\.\d+\.\d+:\d+$", serial):
        return "tcp"
    return "usb"


def _is_adb_error_output(stderr: str) -> bool:
    """
    Some ADB errors are reported on stderr while exit code is still 0.
    This checks for known ADB error prefixes.
    """
    ERROR_MARKERS = ("error:", "failed to", "cannot connect")
    lower = stderr.lower()
    return any(lower.startswith(m) for m in ERROR_MARKERS)


def _is_transient_error(stderr: str) -> bool:
    """
    Distinguish transient (retryable) ADB errors from permanent failures.
    Transient errors are typically USB glitches or daemon restart races.
    """
    TRANSIENT_PATTERNS = (
        "connection reset",
        "device offline",
        "protocol fault",
        "daemon not running",
        "killed",
    )
    lower = stderr.lower()
    return any(p in lower for p in TRANSIENT_PATTERNS)
