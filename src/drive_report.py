from __future__ import annotations

import plistlib
import json
import os
import stat
import struct
import subprocess
import sys
from html import escape

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

from .airs_vfs.drive import DriveError, list_substituted_drives


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
        self.substituted_drives: dict[str, str] = {}
        if sys.platform == "win32":
            try:
                self.substituted_drives = list_substituted_drives()
            except (DriveError, OSError):
                pass
        disk_image_mounts = self._disk_image_mounts()
        volumes = [
            volume
            for volume in self._mounted_volumes()
            if ((volume.isValid() and volume.isReady() and volume.bytesTotal() > 0)
                or self.is_removable_windows_volume(volume.rootPath()))
            and self._is_user_visible_volume(volume, disk_image_mounts)
        ]
        if sys.platform.startswith("linux"):
            volumes = self._unique_linux_volumes(volumes)
        volumes.sort(key=lambda volume: volume.rootPath().casefold())
        device_labels = self._linux_device_labels() if sys.platform.startswith("linux") else {}

        entries = {
            volume.rootPath().replace("\\", "/").rstrip("/").casefold():
            (self._volume_label(volume, device_labels), volume.rootPath(), volume)
            for volume in volumes
        }
        for letter, target in self.substituted_drives.items():
            root = f"{letter}:/"
            entries[root.rstrip("/").casefold()] = (
                f"{letter}: (subst '{target}')", root, QStorageInfo(target)
            )
        ordered_entries = sorted(entries.values(), key=lambda entry: entry[1].casefold())
        self._paths = [root for _label, root, _volume in ordered_entries]
        self._drive_entries = [(label, root) for label, root, _volume in ordered_entries]
        for row in range(self.table.rowCount()):
            for column in range(self.table.columnCount()):
                widget = self.table.cellWidget(row, column)
                if widget is not None:
                    self.table.removeCellWidget(row, column)
                    widget.hide()
                    widget.deleteLater()
        self.table.clearContents()
        self.table.setRowCount(len(ordered_entries))
        self.table.setVisible(bool(ordered_entries))
        self.empty_label.setVisible(not ordered_entries)

        for row, (drive_name, root_path, volume) in enumerate(ordered_entries):
            name_label = QLabel(f"<b>{escape(drive_name)}</b><br><span style='color:#666'>{escape(root_path)}</span>")
            name_label.setContentsMargins(8, 4, 4, 4)
            name_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            name_item = QTableWidgetItem()
            name_item.setToolTip(f"{drive_name}\n{root_path}")
            self.table.setItem(row, 0, name_item)
            self.table.setCellWidget(row, 0, name_label)

            total = max(0, volume.bytesTotal())
            available = max(0, min(total, volume.bytesAvailable()))
            used_percent = max(0, min(100, round((total - available) * 100 / total))) if total else 0
            usage = QProgressBar(self.table)
            usage.setRange(0, 100)
            usage.setValue(used_percent)
            usage.setFormat(f"{used_percent}% used" if total else "Unavailable")
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
                (2, self._format_size(available) if total else "Unknown"),
                (3, self._format_size(total) if total else "Unknown"),
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
    def _mounted_volumes() -> list[QStorageInfo]:
        if os.name != "nt":
            return QStorageInfo.mountedVolumes()
        import ctypes

        # Querying volume info on unreachable mapped network drives blocks for SMB timeouts.
        drive_remote = 4
        drive_no_root_dir = 1
        kernel32 = ctypes.windll.kernel32
        mask = kernel32.GetLogicalDrives()
        volumes: list[QStorageInfo] = []
        for index in range(26):
            if not mask & (1 << index):
                continue
            root = f"{chr(ord('A') + index)}:\\"
            if kernel32.GetDriveTypeW(root) in (drive_remote, drive_no_root_dir):
                continue
            volumes.append(QStorageInfo(root))
        return volumes

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
            import ctypes

            root = root_path.rstrip("\\/") or root_path
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(root_path.replace("/", "\\"))
            kind = {
                2: "Removable drive", 3: "Fixed drive", 4: "Network drive",
                5: "CD/DVD drive", 6: "RAM disk",
            }.get(drive_type, "Drive")
            details = DriveReportWidget._windows_device_details(root_path) if drive_type in (2, 3) else {}
            if details.get("bus") == "USB" and drive_type == 3:
                kind = "USB external drive"
            elif details.get("bus"):
                kind = f"{details['bus']} {kind.lower()}"
            name = volume.name().strip() or details.get("model", "")
            title = f"{root} {name}" if name else root
            description = [kind]
            filesystem = bytes(volume.fileSystemType()).decode("utf-8", errors="replace").strip()
            if filesystem:
                description.append(filesystem)
            if volume.isReadOnly():
                description.append("read-only")
            return f"{title} ({', '.join(description)})"
        if sys.platform.startswith("linux"):
            device = os.path.realpath(bytes(volume.device()).decode("utf-8", errors="replace"))
            return (device_labels or {}).get(device) or volume.displayName().strip() or root_path
        return volume.displayName().strip() or root_path

    @staticmethod
    def _windows_device_details(path: str) -> dict[str, str]:
        root = path.replace("\\", "/")
        if (sys.platform != "win32" or len(root) != 3 or root[1:] != ":/"
                or not root[0].isascii() or not root[0].isalpha()):
            return {}
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        open_device = kernel32.CreateFileW
        open_device.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        open_device.restype = wintypes.HANDLE
        query_device = kernel32.DeviceIoControl
        query_device.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
        ]
        query_device.restype = wintypes.BOOL
        close_device = kernel32.CloseHandle
        close_device.argtypes = [wintypes.HANDLE]
        close_device.restype = wintypes.BOOL
        handle = open_device(f"\\\\.\\{root[:2]}", 0, 7, None, 3, 0, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            return {}
        try:
            query = (ctypes.c_uint32 * 3)(0, 0, 0)
            buffer = ctypes.create_string_buffer(4096)
            returned = wintypes.DWORD()
            if not query_device(
                handle, 0x2D1400, query, ctypes.sizeof(query), buffer,
                ctypes.sizeof(buffer), ctypes.byref(returned), None,
            ) or returned.value < 36:
                return {}
            data = buffer.raw[:returned.value]
            bus_type = struct.unpack_from("<I", data, 28)[0]
            bus = {
                1: "SCSI", 3: "ATA", 7: "USB", 9: "iSCSI", 11: "SATA",
                14: "Virtual", 15: "Virtual", 16: "Storage Spaces", 17: "NVMe", 19: "UFS",
            }.get(bus_type, "")
            model_parts = []
            for position in (12, 16):
                offset = struct.unpack_from("<I", data, position)[0]
                if 36 <= offset < len(data):
                    value = data[offset:].split(b"\0", 1)[0].decode("ascii", errors="replace").strip()
                    if value and value not in model_parts:
                        model_parts.append(value)
            return {"bus": bus, "model": " ".join(model_parts)}
        finally:
            close_device(handle)

    @staticmethod
    def is_removable_windows_volume(path: str) -> bool:
        if sys.platform != "win32":
            return False
        root = path.replace("\\", "/")
        if len(root) != 3 or not root[0].isascii() or not root[0].isalpha() or root[1:] != ":/":
            return False
        import ctypes

        return ctypes.windll.kernel32.GetDriveTypeW(root.replace("/", "\\")) == 2

    @staticmethod
    def eject_windows_volume(path: str) -> None:
        if not DriveReportWidget.is_removable_windows_volume(path):
            raise OSError("Only a removable Windows drive root can be ejected.")
        letter = path[0].upper()
        script = (
            "$ErrorActionPreference = 'Stop'; "
            "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
            f'Get-CimInstance Win32_LogicalDisk -Filter "DeviceID=\'{letter}:\'" | '
            "Get-CimAssociatedInstance -Association Win32_LogicalDiskToPartition | "
            "Get-CimAssociatedInstance -Association Win32_DiskDriveToDiskPartition | "
            "Select-Object -ExpandProperty PNPDeviceID | ConvertTo-Json -Compress"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            device_id = json.loads(result.stdout)
        except ValueError as error:
            raise OSError("Could not identify the removable drive's physical device.") from error
        if not isinstance(device_id, str) or not device_id:
            raise OSError("Could not uniquely identify the removable drive's physical device.")

        import ctypes

        cfgmgr32 = ctypes.WinDLL("cfgmgr32")
        locate = cfgmgr32.CM_Locate_DevNodeW
        locate.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.c_wchar_p, ctypes.c_uint32]
        locate.restype = ctypes.c_uint32
        eject = cfgmgr32.CM_Request_Device_EjectW
        eject.argtypes = [
            ctypes.c_uint32, ctypes.POINTER(ctypes.c_int), ctypes.c_wchar_p,
            ctypes.c_uint32, ctypes.c_uint32,
        ]
        eject.restype = ctypes.c_uint32
        device = ctypes.c_uint32()
        code = locate(ctypes.byref(device), device_id, 0)
        if code:
            raise OSError(f"Could not locate the removable device (Windows code 0x{code:02X}).")
        target = DriveReportWidget._windows_eject_target(cfgmgr32, device.value)
        veto = ctypes.c_int()
        veto_name = ctypes.create_unicode_buffer(260)
        code = eject(target, ctypes.byref(veto), veto_name, len(veto_name), 0)
        if code:
            detail = f": {veto_name.value}" if veto_name.value else ""
            raise OSError(
                "Windows could not safely eject the drive. Close files and applications "
                f"using it and try again (code 0x{code:02X}, veto {veto.value}){detail}"
            )

    @staticmethod
    def _windows_eject_target(cfgmgr32, device: int) -> int:
        import ctypes

        get_property = cfgmgr32.CM_Get_DevNode_Registry_PropertyW
        get_property.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32,
        ]
        get_property.restype = ctypes.c_uint32
        get_parent = cfgmgr32.CM_Get_Parent
        get_parent.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32, ctypes.c_uint32]
        get_parent.restype = ctypes.c_uint32
        visited: set[int] = set()
        while device not in visited:
            visited.add(device)
            capabilities = ctypes.c_uint32()
            length = ctypes.c_uint32(ctypes.sizeof(capabilities))
            code = get_property(
                device, 0x10, None, ctypes.byref(capabilities), ctypes.byref(length), 0
            )
            if code:
                raise OSError(f"Could not read device removal capabilities (Windows code 0x{code:02X}).")
            if capabilities.value & 0x04:
                return device
            parent = ctypes.c_uint32()
            code = get_parent(ctypes.byref(parent), device, 0)
            if code:
                break
            device = parent.value
        raise OSError("Could not find a safely removable parent device.")

    @staticmethod
    def is_removable_macos_volume(path: str) -> bool:
        if sys.platform != "darwin":
            return False
        mount_path = os.path.normpath(path)
        if mount_path == "/" or not mount_path.startswith("/Volumes/"):
            return False
        try:
            result = subprocess.run(
                ["diskutil", "info", "-plist", mount_path],
                capture_output=True,
                check=True,
                timeout=5,
            )
            info = plistlib.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException, ValueError):
            return False

        reported_mount = info.get("MountPoint") if isinstance(info, dict) else None
        if not isinstance(reported_mount, str) or os.path.normpath(reported_mount) != mount_path:
            return False
        return any(
            info.get(key) is True or info.get(key) == 1
            for key in (
                "Ejectable",
                "Removable",
                "RemovableMedia",
                "RemovableMediaOrExternalDevice",
            )
        )

    @staticmethod
    def linux_removable_device(path: str) -> str | None:
        info = DriveReportWidget.linux_removable_device_info(path)
        return info[0] if info else None

    @staticmethod
    def linux_removable_device_info(path: str) -> tuple[str, list[str]] | None:
        if not sys.platform.startswith("linux"):
            return None
        mount_path = os.path.normpath(path)
        if not mount_path.startswith("/") or mount_path == "/":
            return None
        try:
            result = subprocess.run(
                [
                    "lsblk", "--json", "--paths", "--output",
                    "NAME,TYPE,RM,HOTPLUG,MOUNTPOINTS",
                ],
                capture_output=True,
                check=True,
                timeout=5,
            )
            devices = json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError):
            return None

        def enabled(value: object) -> bool:
            return str(value).strip().lower() in {"1", "true", "yes"}

        def mountpoints(device: dict) -> list[str]:
            values = device.get("mountpoints") or [device.get("mountpoint")]
            return [value for value in values if isinstance(value, str)]

        def mounted_devices(device: dict) -> list[str]:
            children = [
                name
                for child in device.get("children", [])
                for name in mounted_devices(child)
            ]
            name = device.get("name") or ""
            if name and mountpoints(device):
                children.append(name)
            return children

        def find_device(device: dict) -> tuple[str, list[str]] | None:
            if device.get("type") == "disk":
                disk = device.get("name") or ""
                removable = enabled(device.get("rm")) or enabled(device.get("hotplug"))
                if removable:
                    all_mountpoints = []

                    def collect_mountpoints(node: dict) -> None:
                        all_mountpoints.extend(mountpoints(node))
                        for child in node.get("children", []):
                            collect_mountpoints(child)

                    collect_mountpoints(device)
                    if mount_path in map(os.path.normpath, all_mountpoints):
                        return disk, mounted_devices(device)

            for child in device.get("children", []):
                match = find_device(child)
                if match:
                    return match
            return None

        for device in devices.get("blockdevices", []):
            match = find_device(device)
            if match:
                return match
        return None

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        size = float(size_bytes)
        for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
            if size < 1024 or unit == "PB":
                return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size_bytes} B"