"""
ui/device_panel.py
====================
Device connection and information panel.

Displays:
  - Device selector dropdown (if multiple devices present)
  - Connect / Disconnect button
  - Device info card (model, OS, serial, build fingerprint)
  - Real-time connection status indicator

Emits signals:
  - device_connected(DeviceInfo)   — after successful connection
  - device_disconnected()          — after user-initiated disconnect
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal, QThread, pyqtSlot
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QGroupBox, QGridLayout, QSizePolicy, QFrame,
)

from core.adb.adb_connector import AdbConnector, NoDeviceError, AdbNotFoundError, MultipleDevicesError
from models.device_info import DeviceInfo
from utils.logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Background worker: device scan
# ─────────────────────────────────────────────────────────────────────────────

class _DeviceScanWorker(QThread):
    """Scans for connected ADB devices in a background thread."""
    devices_found = pyqtSignal(list)   # list[dict]
    error_occurred = pyqtSignal(str)

    def __init__(self, connector: AdbConnector) -> None:
        super().__init__()
        self._connector = connector

    def run(self) -> None:
        try:
            devices = self._connector.list_devices()
            self.devices_found.emit(devices)
        except Exception as exc:
            self.error_occurred.emit(str(exc))


class _ConnectWorker(QThread):
    """Connects to a specific ADB device in a background thread."""
    connected     = pyqtSignal(object)  # DeviceInfo
    error_occurred = pyqtSignal(str)

    def __init__(self, connector: AdbConnector) -> None:
        super().__init__()
        self._connector = connector

    def run(self) -> None:
        try:
            info = self._connector.connect()
            self.connected.emit(info)
        except (NoDeviceError, AdbNotFoundError, MultipleDevicesError) as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Unexpected error: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# DevicePanel widget
# ─────────────────────────────────────────────────────────────────────────────

class DevicePanel(QWidget):
    """
    Left-side panel for device discovery and connection management.

    Signals:
        device_connected(DeviceInfo):  Emitted when a device is successfully connected.
        device_disconnected():         Emitted when the device is disconnected.
        status_message(str):           Human-readable status text for the status bar.
    """

    device_connected    = pyqtSignal(object)   # DeviceInfo
    device_disconnected = pyqtSignal()
    status_message      = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._connector: Optional[AdbConnector] = None
        self._device_info: Optional[DeviceInfo] = None
        self._scan_worker:    Optional[_DeviceScanWorker] = None
        self._connect_worker: Optional[_ConnectWorker]    = None
        self._build_ui()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # ── Status dot + label ──────────────────────────────────────────────
        status_row = QHBoxLayout()
        self._status_dot = QLabel("●")
        self._status_dot.setFixedWidth(18)
        self._status_dot.setStyleSheet("color: #6b7280; font-size: 18px;")
        self._status_label = QLabel("No device connected")
        self._status_label.setStyleSheet("color: #8892a4; font-size: 12px;")
        status_row.addWidget(self._status_dot)
        status_row.addWidget(self._status_label)
        status_row.addStretch()
        layout.addLayout(status_row)

        # ── Device selector ─────────────────────────────────────────────────
        self._device_combo = QComboBox()
        self._device_combo.setPlaceholderText("Select device…")
        self._device_combo.setFixedHeight(34)
        layout.addWidget(self._device_combo)

        # ── Buttons ─────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._scan_btn    = QPushButton("🔍  Scan")
        self._connect_btn = QPushButton("🔌  Connect")
        self._disconnect_btn = QPushButton("⏏  Disconnect")
        self._connect_btn.setObjectName("primaryBtn")
        self._disconnect_btn.setObjectName("dangerBtn")
        self._disconnect_btn.setEnabled(False)
        for b in (self._scan_btn, self._connect_btn, self._disconnect_btn):
            b.setFixedHeight(34)
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        # ── Device info group ───────────────────────────────────────────────
        info_group = QGroupBox("Device Information")
        info_layout = QGridLayout(info_group)
        info_layout.setColumnStretch(1, 1)

        self._fields: dict[str, QLabel] = {}
        props = [
            ("Model",       "model"),
            ("Manufacturer","manufacturer"),
            ("Android",     "android_version"),
            ("API Level",   "sdk_version"),
            ("Serial",      "serial"),
            ("Transport",   "transport_type"),
        ]
        for row, (label_text, key) in enumerate(props):
            key_lbl = QLabel(label_text + ":")
            key_lbl.setStyleSheet("color: #8892a4; font-size: 11px;")
            val_lbl = QLabel("—")
            val_lbl.setStyleSheet("font-size: 12px;")
            val_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            info_layout.addWidget(key_lbl, row, 0)
            info_layout.addWidget(val_lbl, row, 1)
            self._fields[key] = val_lbl

        layout.addWidget(info_group)
        layout.addStretch()

        # ── Wire signals ────────────────────────────────────────────────────
        self._scan_btn.clicked.connect(self._on_scan)
        self._connect_btn.clicked.connect(self._on_connect)
        self._disconnect_btn.clicked.connect(self._on_disconnect)

    # ── Slots ──────────────────────────────────────────────────────────────────

    @pyqtSlot()
    def _on_scan(self) -> None:
        self._scan_btn.setEnabled(False)
        self._scan_btn.setText("Scanning…")
        self.status_message.emit("Scanning for ADB devices…")
        connector = AdbConnector()
        self._scan_worker = _DeviceScanWorker(connector)
        self._scan_worker.devices_found.connect(self._on_devices_found)
        self._scan_worker.error_occurred.connect(self._on_error)
        self._scan_worker.finished.connect(lambda: self._scan_btn.setEnabled(True))
        self._scan_worker.finished.connect(lambda: self._scan_btn.setText("🔍  Scan"))
        self._scan_worker.start()

    @pyqtSlot(list)
    def _on_devices_found(self, devices: list) -> None:
        self._device_combo.clear()
        authorized = [d for d in devices if d.get("state") == "device"]
        if not authorized:
            self._device_combo.setPlaceholderText("No authorised devices found")
            self.status_message.emit("No authorised ADB devices detected.")
            return
        for d in authorized:
            label = f"{d['serial']}  [{d.get('transport', 'usb').upper()}]"
            self._device_combo.addItem(label, userData=d["serial"])
        self.status_message.emit(f"{len(authorized)} device(s) found.")

    @pyqtSlot()
    def _on_connect(self) -> None:
        serial = self._device_combo.currentData()
        self._connect_btn.setEnabled(False)
        self._connect_btn.setText("Connecting…")
        self.status_message.emit("Connecting to device…")
        self._connector = AdbConnector(serial=serial)
        self._connect_worker = _ConnectWorker(self._connector)
        self._connect_worker.connected.connect(self._on_connected)
        self._connect_worker.error_occurred.connect(self._on_error)
        self._connect_worker.finished.connect(lambda: self._connect_btn.setEnabled(True))
        self._connect_worker.finished.connect(lambda: self._connect_btn.setText("🔌  Connect"))
        self._connect_worker.start()

    @pyqtSlot(object)
    def _on_connected(self, info: DeviceInfo) -> None:
        self._device_info = info
        self._set_status_connected(True)
        self._populate_info(info)
        self._disconnect_btn.setEnabled(True)
        self.status_message.emit(f"Connected: {info.display_name()}")
        self.device_connected.emit(info)
        log.info("DevicePanel: connected to %s", info)

    @pyqtSlot()
    def _on_disconnect(self) -> None:
        if self._connector:
            try:
                self._connector.disconnect()
            except Exception:
                pass
        self._connector = None
        self._device_info = None
        self._set_status_connected(False)
        self._clear_info()
        self._disconnect_btn.setEnabled(False)
        self.status_message.emit("Device disconnected.")
        self.device_disconnected.emit()

    @pyqtSlot(str)
    def _on_error(self, msg: str) -> None:
        self._status_label.setText(f"Error: {msg[:80]}")
        self._status_dot.setStyleSheet("color: #ef4444; font-size: 18px;")
        self.status_message.emit(f"ADB error: {msg[:80]}")
        log.error("DevicePanel error: %s", msg)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _set_status_connected(self, connected: bool) -> None:
        if connected:
            self._status_dot.setStyleSheet("color: #22c55e; font-size: 18px;")
            self._status_label.setText("Connected")
            self._status_label.setStyleSheet("color: #22c55e; font-size: 12px;")
        else:
            self._status_dot.setStyleSheet("color: #6b7280; font-size: 18px;")
            self._status_label.setText("No device connected")
            self._status_label.setStyleSheet("color: #8892a4; font-size: 12px;")

    def _populate_info(self, info: DeviceInfo) -> None:
        self._fields["model"].setText(info.model)
        self._fields["manufacturer"].setText(info.manufacturer)
        self._fields["android_version"].setText(info.android_version)
        self._fields["sdk_version"].setText(str(info.sdk_version))
        self._fields["serial"].setText(info.serial)
        self._fields["transport_type"].setText(info.transport_type.upper())

    def _clear_info(self) -> None:
        for label in self._fields.values():
            label.setText("—")

    @property
    def connector(self) -> Optional[AdbConnector]:
        return self._connector

    @property
    def device_info(self) -> Optional[DeviceInfo]:
        return self._device_info
