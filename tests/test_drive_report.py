import stat
import json
import plistlib
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from src.drive_report import DriveReportWidget


class SubstitutedDriveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_removable_drive_with_unavailable_qt_metadata_is_visible(self):
        volume = Mock()
        volume.isValid.return_value = False
        volume.isReady.return_value = False
        volume.bytesTotal.return_value = -1
        volume.bytesAvailable.return_value = -1
        volume.rootPath.return_value = "J:/"
        volume.name.return_value = ""
        volume.isReadOnly.return_value = False
        volume.device.return_value = b""
        volume.fileSystemType.return_value = b""
        with patch("src.drive_report.sys.platform", "win32"), \
                patch.object(DriveReportWidget, "_mounted_volumes", return_value=[volume]), \
                patch.object(DriveReportWidget, "_windows_device_details", return_value={}), \
                patch("src.drive_report.list_substituted_drives", return_value={}), \
                patch("ctypes.windll", create=True) as windll:
            windll.kernel32.GetDriveTypeW.return_value = 2
            widget = DriveReportWidget()
            try:
                self.assertEqual(widget.drive_entries, [("J: (Removable drive)", "J:/")])
                self.assertEqual(widget.table.item(0, 3).text(), "Unknown")
            finally:
                widget.close()
                widget.deleteLater()

    def test_subst_drives_are_added_labeled_and_not_duplicated(self):
        volume = Mock()
        volume.isValid.return_value = True
        volume.isReady.return_value = True
        volume.bytesTotal.return_value = 100
        volume.bytesAvailable.return_value = 60
        volume.rootPath.return_value = "S:/"
        volume.name.return_value = ""
        volume.isReadOnly.return_value = False
        volume.device.return_value = b"local"
        volume.fileSystemType.return_value = b"NTFS"
        storage = Mock()
        storage.mountedVolumes.return_value = [volume]
        storage.return_value = volume
        with patch("src.drive_report.sys.platform", "win32"), \
            patch.object(DriveReportWidget, "_mounted_volumes", return_value=[volume]), \
                patch("src.drive_report.QStorageInfo", storage), \
                patch("src.drive_report.list_substituted_drives",
                      return_value={"S": r"C:\source", "T": r"C:\other"}):
            widget = DriveReportWidget()
            try:
                self.assertEqual(widget.drive_entries, [
                    (r"S: (subst 'C:\source')", "S:/"),
                    (r"T: (subst 'C:\other')", "T:/"),
                ])
                self.assertEqual(widget.drive_count, 2)
            finally:
                widget.close()
                widget.deleteLater()


class WindowsVolumeLabelTests(unittest.TestCase):
    def test_device_descriptor_reports_usb_and_model_and_closes_handle(self):
        import ctypes

        data = bytearray(80)
        struct.pack_into("<I", data, 12, 36)
        struct.pack_into("<I", data, 16, 44)
        struct.pack_into("<I", data, 28, 7)
        data[36:44] = b"Vendor\0\0"
        data[44:53] = b"USB Disk\0"

        def query(_handle, _code, _query, _query_size, buffer, _size, returned, _overlapped):
            ctypes.memmove(buffer, bytes(data), len(data))
            returned._obj.value = len(data)
            return True

        with patch("src.drive_report.sys.platform", "win32"), \
                patch("ctypes.windll", create=True) as windll:
            kernel = windll.kernel32
            kernel.CreateFileW.return_value = 42
            kernel.DeviceIoControl.side_effect = query
            self.assertEqual(DriveReportWidget._windows_device_details("J:/"),
                             {"bus": "USB", "model": "Vendor USB Disk"})
            self.assertEqual(kernel.CreateFileW.call_args.args[:2], (r"\\.\J:", 0))
            self.assertEqual(kernel.DeviceIoControl.call_args.args[1], 0x2D1400)
            kernel.CloseHandle.assert_called_once_with(42)

    def test_unavailable_or_short_device_descriptor_is_optional(self):
        for handle, success, length in ((None, False, 0), (42, False, 0), (42, True, 16)):
            with self.subTest(handle=handle, success=success, length=length), \
                    patch("src.drive_report.sys.platform", "win32"), \
                    patch("ctypes.windll", create=True) as windll:
                kernel = windll.kernel32
                kernel.CreateFileW.return_value = handle

                def query(_handle, _code, _query, _query_size, _buffer, _size, returned, _overlapped):
                    returned._obj.value = length
                    return success

                kernel.DeviceIoControl.side_effect = query
                self.assertEqual(DriveReportWidget._windows_device_details("J:/"), {})
                self.assertEqual(kernel.CloseHandle.called, handle is not None)

    def test_network_and_nonroot_paths_skip_device_query(self):
        with patch("src.drive_report.sys.platform", "win32"), \
                patch("ctypes.windll", create=True) as windll:
            for path in ("//server/share", "J:/folder", "J:"):
                self.assertEqual(DriveReportWidget._windows_device_details(path), {})
            windll.kernel32.CreateFileW.assert_not_called()

    def test_windows_labels_include_volume_name_bus_type_filesystem_and_read_only(self):
        cases = [
            (2, {"bus": "USB", "model": "Verbatim STORE N GO"}, "USB Key", b"exFAT", False,
             "J: USB Key (USB removable drive, exFAT)"),
            (3, {"bus": "NVMe"}, "System", b"NTFS", False,
             "J: System (NVMe fixed drive, NTFS)"),
            (3, {"bus": "USB"}, "Backup", b"NTFS", True,
             "J: Backup (USB external drive, NTFS, read-only)"),
            (5, {}, "Installer", b"UDF", True,
             "J: Installer (CD/DVD drive, UDF, read-only)"),
            (2, {}, "", b"", False, "J: (Removable drive)"),
        ]
        for drive_type, details, name, filesystem, read_only, expected in cases:
            with self.subTest(expected=expected), patch("src.drive_report.sys.platform", "win32"), \
                    patch("ctypes.windll", create=True) as windll, \
                    patch.object(DriveReportWidget, "_windows_device_details", create=True,
                                 return_value=details):
                volume = Mock()
                volume.rootPath.return_value = "J:/"
                volume.name.return_value = name
                volume.fileSystemType.return_value = filesystem
                volume.isReadOnly.return_value = read_only
                windll.kernel32.GetDriveTypeW.return_value = drive_type
                self.assertEqual(DriveReportWidget._volume_label(volume), expected)


class WindowsEjectTests(unittest.TestCase):
    def test_only_removable_drive_roots_are_ejectable(self):
        with patch("src.drive_report.sys.platform", "win32"), \
                patch("ctypes.windll", create=True) as windll:
            windll.kernel32.GetDriveTypeW.return_value = 2
            for path in ("J:/", "j:\\"):
                self.assertTrue(DriveReportWidget.is_removable_windows_volume(path))
            for path in ("J:/folder", "//server/share", "J:", "/", "1:/"):
                self.assertFalse(DriveReportWidget.is_removable_windows_volume(path))
            windll.kernel32.GetDriveTypeW.return_value = 3
            self.assertFalse(DriveReportWidget.is_removable_windows_volume("C:/"))

    def test_safe_eject_reports_success_and_windows_veto(self):
        for code in (0, 0x17):
            with self.subTest(code=code), \
                    patch.object(DriveReportWidget, "is_removable_windows_volume", return_value=True), \
                    patch("src.drive_report.subprocess.run", return_value=SimpleNamespace(
                        stdout=json.dumps("USBSTOR\\DISK\\USB123")
                    )) as run, patch("ctypes.WinDLL", create=True) as library:
                native = library.return_value
                native.CM_Locate_DevNodeW.return_value = 0
                def capabilities(_device, _property, _type, buffer, _length, _flags):
                    buffer._obj.value = 4
                    return 0
                native.CM_Get_DevNode_Registry_PropertyW.side_effect = capabilities
                native.CM_Request_Device_EjectW.return_value = code
                if code:
                    with self.assertRaisesRegex(OSError, "safely eject"):
                        DriveReportWidget.eject_windows_volume("J:/")
                else:
                    DriveReportWidget.eject_windows_volume("J:/")
                self.assertIn("DeviceID='J:'", run.call_args.args[0][-1])
                native.CM_Request_Device_EjectW.assert_called_once()
                self.assertEqual(native.CM_Locate_DevNodeW.call_args.args[1],
                                 "USBSTOR\\DISK\\USB123")

    def test_unidentified_device_is_never_ejected(self):
        for output in ("", "null", '["disk1","disk2"]'):
            with self.subTest(output=output), \
                    patch.object(DriveReportWidget, "is_removable_windows_volume", return_value=True), \
                    patch("src.drive_report.subprocess.run", return_value=SimpleNamespace(stdout=output)), \
                    patch("ctypes.WinDLL", create=True) as library:
                with self.assertRaises(OSError):
                    DriveReportWidget.eject_windows_volume("J:/")
                library.assert_not_called()

    def test_eject_targets_removable_usb_parent_not_nonremovable_disk(self):
        with patch.object(DriveReportWidget, "is_removable_windows_volume", return_value=True), \
                patch("src.drive_report.subprocess.run", return_value=SimpleNamespace(
                    stdout=json.dumps("USBSTOR\\DISK\\USB123")
                )), patch("ctypes.WinDLL", create=True) as library:
            native = library.return_value

            def locate(device, _identity, _flags):
                device._obj.value = 10
                return 0

            def capabilities(device, _property, _type, buffer, _length, _flags):
                buffer._obj.value = 4 if device == 20 else 0
                return 0

            def parent(device, child, _flags):
                self.assertEqual(child, 10)
                device._obj.value = 20
                return 0

            native.CM_Locate_DevNodeW.side_effect = locate
            native.CM_Get_DevNode_Registry_PropertyW.side_effect = capabilities
            native.CM_Get_Parent.side_effect = parent
            native.CM_Request_Device_EjectW.return_value = 0
            DriveReportWidget.eject_windows_volume("J:/")
            self.assertEqual(native.CM_Request_Device_EjectW.call_args.args[0], 20)

    def test_fixed_drive_is_never_ejected(self):
        with patch.object(DriveReportWidget, "is_removable_windows_volume", return_value=False), \
                patch("src.drive_report.subprocess.run") as run:
            with self.assertRaises(OSError):
                DriveReportWidget.eject_windows_volume("C:/")
            run.assert_not_called()

    def test_unresolved_removal_capabilities_never_request_eject(self):
        for property_code, parent_code in ((0x25, 0), (0, 0x0D), (0, 0)):
            with self.subTest(property_code=property_code, parent_code=parent_code), \
                    patch.object(DriveReportWidget, "is_removable_windows_volume", return_value=True), \
                    patch("src.drive_report.subprocess.run", return_value=SimpleNamespace(
                        stdout=json.dumps("USBSTOR\\DISK\\USB123")
                    )), patch("ctypes.WinDLL", create=True) as library:
                native = library.return_value
                native.CM_Locate_DevNodeW.return_value = 0
                native.CM_Get_DevNode_Registry_PropertyW.return_value = property_code
                native.CM_Get_Parent.return_value = parent_code
                with self.assertRaises(OSError):
                    DriveReportWidget.eject_windows_volume("J:/")
                native.CM_Request_Device_EjectW.assert_not_called()


class LinuxVolumeVisibilityTests(unittest.TestCase):
    def test_disk_names_are_inherited_by_partitions(self):
        metadata = {"blockdevices": [{
            "name": "/dev/sdb", "type": "disk", "vendor": "JMicron ",
            "model": "Generic ", "children": [{"name": "/dev/sdb2", "type": "part"}],
        }]}
        with patch("src.drive_report.subprocess.run", return_value=SimpleNamespace(
            stdout=json.dumps(metadata)
        )):
            labels = DriveReportWidget._linux_device_labels()
        self.assertEqual(labels["/dev/sdb2"], "JMicron Generic (sdb2)")
        volume = Mock()
        volume.device.return_value = b"/dev/sdb2"
        volume.rootPath.return_value = "/media/user/disk"
        with patch("src.drive_report.sys.platform", "linux"):
            self.assertEqual(DriveReportWidget._volume_label(volume, labels), "JMicron Generic (sdb2)")

    def test_missing_disk_metadata_is_optional(self):
        with patch("src.drive_report.subprocess.run", side_effect=FileNotFoundError()):
            self.assertEqual(DriveReportWidget._linux_device_labels(), {})
        with patch("src.drive_report.subprocess.run", return_value=SimpleNamespace(stdout="invalid")):
            self.assertEqual(DriveReportWidget._linux_device_labels(), {})

    def visible(self, device, filesystem, root, mode=stat.S_IFBLK):
        volume = Mock()
        volume.device.return_value = device.encode()
        volume.fileSystemType.return_value = filesystem.encode()
        volume.rootPath.return_value = root
        with patch("src.drive_report.sys.platform", "linux"), patch(
            "src.drive_report.os.stat", return_value=SimpleNamespace(st_mode=mode)
        ):
            return DriveReportWidget._is_user_visible_volume(volume, set())

    def test_disk_partitions_and_removable_devices_are_visible(self):
        for device, root in (
            ("/dev/sda2", "/"),
            ("/dev/nvme0n1p3", "/home"),
            ("/dev/sdb1", "/media/user/USB"),
            ("/dev/sdc1", "/run/media/user/USB"),
            ("/dev/mapper/system-root", "/"),
            ("/dev/disk/by-label/MyDrive", "/mnt/data"),
        ):
            with self.subTest(device=device, root=root):
                self.assertTrue(self.visible(device, "ext4", root))

    def test_virtual_and_network_mounts_are_hidden(self):
        for device, filesystem in (
            ("tmpfs", "tmpfs"),
            ("proc", "proc"),
            ("overlay", "overlay"),
            ("server:/share", "nfs4"),
            ("/dev/loop0", "ext4"),
            ("/dev/ram0", "ext4"),
            ("/dev/zram0", "ext4"),
            ("/dev/sda1", "squashfs"),
        ):
            with self.subTest(device=device):
                mode = stat.S_IFDIR if device in ("tmpfs", "proc", "overlay") else stat.S_IFBLK
                self.assertFalse(self.visible(device, filesystem, "/mnt/virtual", mode))

    def test_missing_or_inaccessible_devices_are_hidden(self):
        volume = Mock()
        volume.device.return_value = b"/dev/missing"
        volume.fileSystemType.return_value = b"ext4"
        volume.rootPath.return_value = "/mnt/missing"
        for error in (FileNotFoundError(), PermissionError()):
            with self.subTest(error=type(error)), patch(
                "src.drive_report.sys.platform", "linux"
            ), patch("src.drive_report.os.stat", side_effect=error):
                self.assertFalse(DriveReportWidget._is_user_visible_volume(volume, set()))

    def test_device_path_case_is_preserved(self):
        volume = Mock()
        volume.device.return_value = b"/dev/disk/by-label/MyDrive"
        volume.fileSystemType.return_value = b"ext4"
        volume.rootPath.return_value = "/mnt/data"
        with patch("src.drive_report.sys.platform", "linux"), patch(
            "src.drive_report.os.stat", return_value=SimpleNamespace(st_mode=stat.S_IFBLK)
        ) as device_stat:
            self.assertTrue(DriveReportWidget._is_user_visible_volume(volume, set()))
            device_stat.assert_called_once_with("/dev/disk/by-label/MyDrive")

    def test_repeated_mounts_of_one_partition_are_collapsed(self):
        volumes = []
        for device, root in (
            ("/dev/sda2", "/mnt/root-copy"),
            ("/dev/sda2", "/"),
            ("/dev/sda1", "/boot/efi"),
            ("/dev/sdb2", "/media/user/disk"),
            ("/dev/sdb2", "/home/user/source"),
        ):
            volume = Mock()
            volume.device.return_value = device.encode()
            volume.rootPath.return_value = root
            volumes.append(volume)
        unique_volumes = DriveReportWidget._unique_linux_volumes(volumes)
        self.assertEqual(
            [volume.rootPath() for volume in unique_volumes],
            ["/", "/boot/efi", "/media/user/disk"],
        )


    def test_removable_mount_resolves_to_parent_disk(self):
        metadata = {"blockdevices": [{
            "name": "/dev/sdb", "type": "disk", "rm": True, "hotplug": True,
            "children": [{
                "name": "/dev/sdb1", "type": "part",
                "mountpoints": ["/media/user/USB"],
                "children": [{
                    "name": "/dev/mapper/usb", "type": "crypt",
                    "mountpoints": ["/media/user/USB/data"],
                }],
            }],
        }]}
        with patch("src.drive_report.sys.platform", "linux"), patch(
            "src.drive_report.subprocess.run",
            return_value=SimpleNamespace(stdout=json.dumps(metadata)),
        ) as run:
            self.assertEqual(
                DriveReportWidget.linux_removable_device("/media/user/USB"), "/dev/sdb"
            )
            self.assertEqual(
                DriveReportWidget.linux_removable_device_info("/media/user/USB"),
                ("/dev/sdb", ["/dev/mapper/usb", "/dev/sdb1"]),
            )
        self.assertIn("RM,HOTPLUG,MOUNTPOINTS", run.call_args.args[0][-1])

    def test_internal_unmounted_and_root_volumes_are_not_ejectable(self):
        metadata = {"blockdevices": [{
            "name": "/dev/sda", "type": "disk", "rm": False, "hotplug": False,
            "children": [{
                "name": "/dev/sda1", "type": "part",
                "mountpoints": ["/mnt/internal"],
            }],
        }, {
            "name": "/dev/sdb", "type": "disk", "rm": "1", "hotplug": "0",
            "children": [{
                "name": "/dev/sdb1", "type": "part", "mountpoints": [None],
            }],
        }]}
        with patch("src.drive_report.sys.platform", "linux"), patch(
            "src.drive_report.subprocess.run",
            return_value=SimpleNamespace(stdout=json.dumps(metadata)),
        ):
            self.assertIsNone(DriveReportWidget.linux_removable_device("/mnt/internal"))
            self.assertIsNone(DriveReportWidget.linux_removable_device("/"))


class MacOSRemovableVolumeTests(unittest.TestCase):
    def test_removable_volume_requires_matching_mount_and_ejectability(self):
        path = "/Volumes/USB Stick"
        info = {
            "MountPoint": path,
            "Ejectable": 1,
            "Removable": 0,
        }
        with patch("src.drive_report.sys.platform", "darwin"), patch(
            "src.drive_report.subprocess.run",
            return_value=SimpleNamespace(stdout=plistlib.dumps(info)),
        ) as run:
            self.assertTrue(DriveReportWidget.is_removable_macos_volume(path))
        run.assert_called_once_with(
            ["diskutil", "info", "-plist", path],
            capture_output=True,
            check=True,
            timeout=5,
        )

    def test_internal_or_mismatched_volume_is_not_removable(self):
        for info in (
            {"MountPoint": "/Volumes/External", "Ejectable": 0, "Removable": 0},
            {"MountPoint": "/Volumes/Other", "Ejectable": 1},
        ):
            with self.subTest(info=info), patch("src.drive_report.sys.platform", "darwin"), patch(
                "src.drive_report.subprocess.run",
                return_value=SimpleNamespace(stdout=plistlib.dumps(info)),
            ):
                self.assertFalse(
                    DriveReportWidget.is_removable_macos_volume("/Volumes/External")
                )

    def test_non_volume_paths_skip_diskutil_probe(self):
        with patch("src.drive_report.sys.platform", "darwin"), patch(
            "src.drive_report.subprocess.run"
        ) as run:
            self.assertFalse(DriveReportWidget.is_removable_macos_volume("/"))
            self.assertFalse(DriveReportWidget.is_removable_macos_volume("/Users/me"))
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()