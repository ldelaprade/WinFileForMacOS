from __future__ import annotations

import plistlib
import json
import os
import stat
import subprocess
import sys

from PySide6.QtCore import Qt, QStorageInfo, Signal
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class DriveReportWidget(QWidget):
    drive_activated = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        heading = QLabel("Drives", self)
        heading.setStyleSheet("font-size: 16px; font-weight: bold; padding: 2px 4px;")
        layout.addWidget(heading)

        self.empty_label = QLabel("No mounted drives are available.", self)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.hide()
        layout.addWidget(self.empty_label)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["Drive", "Used", "Available", "Capacity"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.cellDoubleClicked.connect(self._activate_row)
        self.table.cellActivated.connect(self._activate_row)
        layout.addWidget(self.table)

        self._paths: list[str] = []
        self._drive_entries: list[tuple[str, str]] = []
        self.refresh_drives()

    @property
    def drive_count(self) -> int:
        return len(self._paths)

    @property
    def drive_entries(self) -> list[tuple[str, str]]:
        return self._drive_entries.copy()

    def refresh_drives(self) -> None:
        disk_image_mounts = self._disk_image_mounts()
        volumes = [
            volume
            for volume in QStorageInfo.mountedVolumes()
            if volume.isValid()
            and volume.isReady()
            and volume.bytesTotal() > 0
            and self._is_user_visible_volume(volume, disk_image_mounts)
        ]
        if sys.platform.startswith("linux"):
            volumes = self._unique_linux_volumes(volumes)
        volumes.sort(key=lambda volume: volume.rootPath().casefold())
        device_labels = self._linux_device_labels() if sys.platform.startswith("linux") else {}

        self._paths = [volume.rootPath() for volume in volumes]
        self._drive_entries = [
            (self._volume_label(volume, device_labels), volume.rootPath()) for volume in volumes
        ]
        for row in range(self.table.rowCount()):
            for column in range(self.table.columnCount()):
                widget = self.table.cellWidget(row, column)
                if widget is not None:
                    self.table.removeCellWidget(row, column)
                    widget.hide()
                    widget.deleteLater()
        self.table.clearContents()
        self.table.setRowCount(len(volumes))
        self.table.setVisible(bool(volumes))
        self.empty_label.setVisible(not volumes)

        for row, volume in enumerate(volumes):
            root_path = volume.rootPath()
            drive_name = self._volume_label(volume, device_labels)
            name_label = QLabel(f"<b>{drive_name}</b><br><span style='color:#666'>{root_path}</span>")
            name_label.setContentsMargins(8, 4, 4, 4)
            name_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            name_item = QTableWidgetItem()
            name_item.setToolTip(f"{drive_name}\n{root_path}")
            self.table.setItem(row, 0, name_item)
            self.table.setCellWidget(row, 0, name_label)

            total = volume.bytesTotal()
            available = max(0, min(total, volume.bytesAvailable()))
            used_percent = max(0, min(100, round((total - available) * 100 / total)))
            usage = QProgressBar(self.table)
            usage.setRange(0, 100)
            usage.setValue(used_percent)
            usage.setFormat(f"{used_percent}% used")
            usage.setTextVisible(True)
            usage.setFixedHeight(22)
            usage.setStyleSheet(
                "QProgressBar { border: 1px solid #b8ad8a; border-radius: 3px; "
                "background: #fffdf3; text-align: center; color: #262626; }"
                "QProgressBar::chunk { background: "
                f"{'#c85a4a' if used_percent >= 90 else '#d7a83c' if used_percent >= 75 else '#79a85b'};"
                " border-radius: 2px; }"
            )
            usage.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            usage_item = QTableWidgetItem()
            usage_item.setToolTip(f"{used_percent}% used")
            self.table.setItem(row, 1, usage_item)
            self.table.setCellWidget(row, 1, usage)

            for column, value in (
                (2, self._format_size(available)),
                (3, self._format_size(total)),
            ):
                item = QTableWidgetItem(value)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                self.table.setItem(row, column, item)

            self.table.setRowHeight(row, 58)

    def _activate_row(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._paths):
            self.drive_activated.emit(self._paths[row])

    @staticmethod
    def _is_user_visible_volume(
        volume: QStorageInfo,
        disk_image_mounts: set[str],
    ) -> bool:
        root_path = volume.rootPath()
        normalized = root_path.rstrip("/") or "/"
        device = bytes(volume.device()).decode("utf-8", errors="replace").casefold()
        filesystem = bytes(volume.fileSystemType()).decode(
            "utf-8",
            errors="replace",
        ).casefold()

        if normalized in disk_image_mounts:
            return False
        if device.startswith(("//", "\\\\")) and not (
            sys.platform == "win32" and device.startswith("\\\\?\\volume{")
        ):
            return False
        if any(token in filesystem for token in ("smb", "nfs", "cifs", "afp", "webdav", "sshfs")):
            return False

        if sys.platform == "darwin":
            return normalized == "/" or normalized.startswith("/Volumes/")
        if sys.platform.startswith("linux"):
            if device.startswith(("/dev/loop", "/dev/ram", "/dev/zram")) or filesystem == "squashfs":
                return False
            device_path = bytes(volume.device()).decode("utf-8", errors="replace")
            try:
                return stat.S_ISBLK(os.stat(device_path).st_mode)
            except OSError:
                return False
        return True

    @staticmethod
    def _unique_linux_volumes(volumes: list[QStorageInfo]) -> list[QStorageInfo]:
        devices: set[str] = set()
        unique_volumes: list[QStorageInfo] = []
        for volume in sorted(volumes, key=lambda volume: volume.rootPath() != "/"):
            device = os.path.realpath(bytes(volume.device()).decode("utf-8", errors="replace"))
            if device not in devices:
                devices.add(device)
                unique_volumes.append(volume)
        return unique_volumes

    @staticmethod
    def _linux_device_labels() -> dict[str, str]:
        try:
            result = subprocess.run(
                ["lsblk", "--json", "--paths", "--output", "NAME,TYPE,VENDOR,MODEL"],
                capture_output=True,
                check=True,
                timeout=5,
            )
            devices = json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError):
            return {}

        labels: dict[str, str] = {}

        def collect(device: dict, parent_label: str = "") -> None:
            model = (device.get("model") or "").strip()
            vendor = (device.get("vendor") or "").strip()
            label = parent_label
            if model:
                label = f"{vendor} {model}" if vendor and vendor != "ATA" else model
            name = device.get("name") or ""
            if name and label:
                labels[os.path.realpath(name)] = (
                    label if device.get("type") == "disk" else f"{label} ({os.path.basename(name)})"
                )
            for child in device.get("children", []):
                collect(child, label)

        for device in devices.get("blockdevices", []):
            collect(device)
        return labels

    @staticmethod
    def _disk_image_mounts() -> set[str]:
        if sys.platform != "darwin":
            return set()
        try:
            result = subprocess.run(
                ["hdiutil", "info", "-plist"],
                capture_output=True,
                check=True,
                timeout=5,
            )
            info = plistlib.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException):
            return set()

        return {
            entity["mount-point"].rstrip("/") or "/"
            for image in info.get("images", [])
            for entity in image.get("system-entities", [])
            if entity.get("mount-point")
        }

    @staticmethod
    def _volume_label(volume: QStorageInfo, device_labels: dict[str, str] | None = None) -> str:
        root_path = volume.rootPath()
        if sys.platform == "win32":
            return root_path.rstrip("\\/") or root_path
        if sys.platform.startswith("linux"):
            device = os.path.realpath(bytes(volume.device()).decode("utf-8", errors="replace"))
            return (device_labels or {}).get(device) or volume.displayName().strip() or root_path
        return volume.displayName().strip() or root_path

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        size = float(size_bytes)
        for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
            if size < 1024 or unit == "PB":
                return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size_bytes} B"