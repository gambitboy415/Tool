"""
models/device_info.py
=====================
Immutable dataclass representing a connected Android device.
Populated once at connection time; never mutated afterward.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class DeviceInfo:
    """
    Snapshot of an Android device's identity at the time of connection.

    Attributes:
        serial:         ADB device serial (e.g. "emulator-5554", "R5CW3xxxxx")
        model:          Human-readable device model (e.g. "Pixel 6")
        manufacturer:   Device manufacturer (e.g. "Google")
        android_version: Android release version string (e.g. "13")
        sdk_version:    Android API level as integer (e.g. 33)
        build_fingerprint: Full build fingerprint for exact identification
        connected_at:   UTC timestamp when this device was detected
        transport_type: "usb" | "tcp" | "emulator"
    """

    serial: str
    model: str
    manufacturer: str
    android_version: str
    sdk_version: int
    build_fingerprint: str
    connected_at: datetime
    transport_type: str = "usb"

    def display_name(self) -> str:
        """Returns a human-readable label, e.g. 'Google Pixel 6 (Android 13)'."""
        return f"{self.manufacturer} {self.model} (Android {self.android_version})"

    def __str__(self) -> str:
        return f"DeviceInfo({self.serial}, {self.display_name()})"
