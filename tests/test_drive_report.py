import stat
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from src.drive_report import DriveReportWidget


class SubstitutedDriveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_subst_drives_are_added_labeled_and_not_duplicated(self):
        volume = Mock()
        volume.isValid.return_value = True
        volume.isReady.return_value = True
        volume.bytesTotal.return_value = 100
        volume.bytesAvailable.return_value = 60
        volume.rootPath.return_value = "S:/"
        volume.device.return_value = b"local"
        volume.fileSystemType.return_value = b"NTFS"
        storage = Mock()
        storage.mountedVolumes.return_value = [volume]
        storage.return_value = volume
        with patch("src.drive_report.sys.platform", "win32"), \
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


if __name__ == "__main__":
    unittest.main()