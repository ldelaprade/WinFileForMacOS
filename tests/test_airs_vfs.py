import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from src.airs_vfs import (VfsError, create_vfs_from_folder, find_recording_files,
                          folder_has_recordings, read_vfs_index)
from src.airs_vfs.drive import DriveError, available_drive_letters, unmount_drive
from src.airs_vfs.vfs_image import CONFIG_SIZE, STREAM_START_SIZE, fletcher16


class DriveLetterTests(unittest.TestCase):
    def test_filesystem_root_cannot_be_cleared(self):
        root = Path(tempfile.gettempdir()).anchor
        with patch("src.airs_vfs.drive.list_substituted_drives", return_value={"S": root}), \
                patch("src.airs_vfs.drive.os.scandir") as scan, \
                patch("src.airs_vfs.drive._subst") as run:
            with self.assertRaises(DriveError):
                unmount_drive("S", delete_contents=True)
            scan.assert_not_called()
            run.assert_not_called()

    def test_delete_contents_before_unmapping_and_preserve_target_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "nested").mkdir()
            (folder / "nested" / "recording.bit").write_bytes(b"recording")
            (folder / "notes.txt").write_text("notes")

            def subst(*args):
                self.assertEqual(args, ("S:", "/D"))
                self.assertTrue(folder.is_dir())
                self.assertEqual(list(folder.iterdir()), [])
                return SimpleNamespace(returncode=0)

            with patch("src.airs_vfs.drive.list_substituted_drives", return_value={"S": directory}), \
                    patch("src.airs_vfs.drive._subst", side_effect=subst) as run:
                unmount_drive("S", delete_contents=True, expected_target=directory)
                run.assert_called_once()

    def test_unmapping_preserves_contents_by_default(self):
        with patch("src.airs_vfs.drive.list_substituted_drives", return_value={"S": "target"}), \
                patch("src.airs_vfs.drive.os.scandir") as scan, \
                patch("src.airs_vfs.drive._subst", return_value=SimpleNamespace(returncode=0)) as run:
            unmount_drive("S")
            scan.assert_not_called()
            run.assert_called_once_with("S:", "/D")

    def test_deletion_failure_does_not_unmap(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("src.airs_vfs.drive.list_substituted_drives", return_value={"S": directory}), \
                    patch("src.airs_vfs.drive.os.scandir", side_effect=PermissionError("denied")), \
                    patch("src.airs_vfs.drive._subst") as run:
                with self.assertRaises(DriveError):
                    unmount_drive("S", delete_contents=True)
                run.assert_not_called()

    def test_changed_or_missing_mapping_is_not_deleted(self):
        for mappings in ({}, {"S": "different"}):
            with self.subTest(mappings=mappings), \
                    patch("src.airs_vfs.drive.list_substituted_drives", return_value=mappings), \
                    patch("src.airs_vfs.drive.os.scandir") as scan, \
                    patch("src.airs_vfs.drive._subst") as run:
                with self.assertRaises(DriveError):
                    unmount_drive("S", delete_contents=True, expected_target="original")
                scan.assert_not_called()
                run.assert_not_called()

    def test_windows_excludes_assigned_drives_without_accessible_roots(self):
        with patch("src.airs_vfs.drive.os.name", "nt"), \
                patch("src.airs_vfs.drive.ctypes.windll", create=True) as windll, \
                patch("src.airs_vfs.drive.os.path.exists", return_value=False) as exists:
            windll.kernel32.GetLogicalDrives.return_value = (1 << 2) | (1 << 3) | (1 << 25)
            self.assertEqual(available_drive_letters(), list("EFGHIJKLMNOPQRSTUVWXY"))
            exists.assert_not_called()

    def test_windows_enumeration_failure(self):
        with patch("src.airs_vfs.drive.os.name", "nt"), \
                patch("src.airs_vfs.drive.ctypes.windll", create=True) as windll:
            windll.kernel32.GetLogicalDrives.return_value = 0
            with self.assertRaises(DriveError):
                available_drive_letters()

    def test_non_windows_preserves_root_checks(self):
        with patch("src.airs_vfs.drive.os.name", "posix"), \
                patch("src.airs_vfs.drive.os.path.exists",
                      side_effect=lambda path: path == "C:\\"):
            self.assertEqual(available_drive_letters(), list("DEFGHIJKLMNOPQRSTUVWXYZ"))


class VfsImageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.src = Path(self._tmp.name, "src")
        self.dst = Path(self._tmp.name, "dst")
        self.src.mkdir()

    def _write(self, name, size):
        data = bytes((i * 7) % 256 for i in range(size))
        (self.src / name).write_bytes(data)
        return data

    def test_round_trip_and_layout(self):
        a = self._write("VIS-000001-37749-001.bit", 5854)
        self._write("VIS-000001-37749-001.idx", 512 - STREAM_START_SIZE)  # exact fit edge case
        self._write("notes.txt", 10)
        vfs = create_vfs_from_folder(self.src, self.dst)
        self.assertEqual(vfs.name, "APVDF000.vfs")
        self.assertEqual(vfs.stat().st_size % 512, 0)
        index = read_vfs_index(vfs)
        self.assertEqual([e["name"] for e in index],
                         ["VIS-000001-37749-001.bit", "VIS-000001-37749-001.idx"])
        self.assertEqual(index[0]["size"], len(a))
        raw = vfs.read_bytes()
        payload = raw[CONFIG_SIZE + STREAM_START_SIZE:CONFIG_SIZE + STREAM_START_SIZE + len(a)]
        self.assertEqual(hashlib.md5(payload).digest(), hashlib.md5(a).digest())

    def test_detection(self):
        self.assertFalse(folder_has_recordings(self.src))
        self._write("readme.txt", 1)
        self.assertFalse(folder_has_recordings(self.src))
        self._write("VIS-FF1N-02241-001.img", 1)
        self.assertTrue(folder_has_recordings(self.src))
        self.assertEqual(len(find_recording_files(self.src)), 1)

    def test_errors(self):
        with self.assertRaises(VfsError):
            create_vfs_from_folder(self.src, self.dst)
        self._write("VIS-000001-37749-001.bit", 1)
        with self.assertRaises(VfsError):
            create_vfs_from_folder(self.src, self.src)
        self.assertFalse(list(self.dst.glob("*")))

    def test_fletcher_known_value(self):
        self.assertEqual(fletcher16(b"\x01\x00\x02\x00"), (4 << 16) | 3)


if __name__ == "__main__":
    unittest.main()
