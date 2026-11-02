import os
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from src.folder_archive import PackError, find_mkisofs, pack_folder, validate_destination


class FolderArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "folder with spaces"
        (self.source / "empty").mkdir(parents=True)
        (self.source / "nested").mkdir()
        (self.source / "nested/data.txt").write_bytes(b"archive contents")
        (self.source / ".hidden").write_bytes(b"hidden")
        self.output = self.directory / "output.archive"

    def test_zip_tar_and_gzip_contain_files_hidden_files_and_empty_folders(self):
        for archive_format in ("zip", "tar", "gzip"):
            with self.subTest(archive_format=archive_format):
                result = pack_folder(str(self.source), str(self.output), archive_format)
                self.assertEqual(result, str(self.output))
                if archive_format == "zip":
                    with zipfile.ZipFile(self.output) as archive:
                        self.assertIsNone(archive.testzip())
                        self.assertEqual(archive.read("nested/data.txt"), b"archive contents")
                        self.assertIn("empty/", archive.namelist())
                        self.assertIn(".hidden", archive.namelist())
                else:
                    with tarfile.open(self.output) as archive:
                        self.assertTrue(archive.getmember("empty").isdir())
                        self.assertEqual(archive.extractfile("nested/data.txt").read(), b"archive contents")
                        self.assertIn(".hidden", archive.getnames())
                    if archive_format == "gzip":
                        self.assertEqual(self.output.read_bytes()[:2], b"\x1f\x8b")
                self.assertEqual(list(self.directory.glob(".retrofile-pack-*")), [])

    def test_empty_folder_packs_successfully(self):
        for archive_format in ("zip", "tar", "gzip"):
            with self.subTest(archive_format=archive_format):
                pack_folder(str(self.source / "empty"), str(self.output), archive_format)
                self.assertTrue(self.output.is_file())

    @unittest.skipIf(os.name == "nt", "Symlinks require Windows privileges")
    def test_symlinks_are_preserved_not_followed(self):
        outside = self.directory / "outside"
        outside.mkdir()
        (outside / "secret").write_text("do not include")
        (self.source / "link").symlink_to(outside, target_is_directory=True)
        (self.source / "broken").symlink_to(self.directory / "missing")
        for archive_format in ("zip", "tar", "gzip"):
            with self.subTest(archive_format=archive_format):
                pack_folder(str(self.source), str(self.output), archive_format)
                if archive_format == "zip":
                    with zipfile.ZipFile(self.output) as archive:
                        self.assertTrue(stat.S_ISLNK(archive.getinfo("link").external_attr >> 16))
                        self.assertEqual(archive.read("link").decode(), str(outside))
                        self.assertNotIn("link/secret", archive.namelist())
                        self.assertIn("broken", archive.namelist())
                else:
                    with tarfile.open(self.output) as archive:
                        self.assertTrue(archive.getmember("link").issym())
                        self.assertNotIn("link/secret", archive.getnames())
                        self.assertTrue(archive.getmember("broken").issym())

    def test_source_and_nested_destinations_rejected(self):
        for destination in (self.source, self.source / "output.zip"):
            with self.subTest(destination=destination):
                with self.assertRaises(PackError):
                    validate_destination(self.source, destination)

    @unittest.skipIf(os.name == "nt", "Symlinks require Windows privileges")
    def test_destination_symlink_into_source_rejected(self):
        link = self.directory / "alias"
        link.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(PackError):
            validate_destination(self.source, link / "output.zip")

    def test_case_alias_is_rejected_on_case_insensitive_volumes(self):
        alias = self.source.with_name(self.source.name.swapcase())
        if not alias.is_dir():
            self.skipTest("Case-sensitive filesystem")
        with self.assertRaises(PackError):
            validate_destination(self.source, alias / "output.zip")

    def test_missing_source_or_output_folder_rejected(self):
        with self.assertRaises(PackError):
            validate_destination(self.directory / "missing", self.output)
        with self.assertRaises(PackError):
            validate_destination(self.source, self.directory / "missing/output.zip")

    def test_invalid_format_and_missing_iso_tool_report_errors(self):
        for archive_format in ("invalid", "iso"):
            with self.subTest(archive_format=archive_format), self.assertRaises(PackError):
                pack_folder(str(self.source), str(self.output), archive_format)
        self.assertFalse(self.output.exists())

    def test_read_error_preserves_existing_output_and_removes_partial(self):
        self.output.write_bytes(b"previous")
        for archive_format in ("zip", "tar", "gzip"):
            with self.subTest(archive_format=archive_format):
                with patch("src.folder_archive._entries", side_effect=PermissionError("denied")):
                    with self.assertRaisesRegex(PackError, "denied"):
                        pack_folder(str(self.source), str(self.output), archive_format)
                self.assertEqual(self.output.read_bytes(), b"previous")
                self.assertEqual(list(self.directory.glob(".retrofile-pack-*")), [])

    def test_walk_permission_error_is_not_silently_skipped(self):
        def walk(root, **kwargs):
            kwargs["onerror"](PermissionError("unreadable directory"))
            return iter(())

        with patch("src.folder_archive.os.walk", side_effect=walk):
            with self.assertRaisesRegex(PackError, "unreadable directory"):
                pack_folder(str(self.source), str(self.output), "zip")

    def test_iso_failures_preserve_existing_output(self):
        self.output.write_bytes(b"previous")

        def run(arguments, **kwargs):
            kwargs["stdout"].write(b"Cannot read source")
            return subprocess.CompletedProcess(arguments, 1)

        with patch("src.folder_archive.subprocess.run", side_effect=run):
            with self.assertRaisesRegex(PackError, "Cannot read source"):
                pack_folder(str(self.source), str(self.output), "iso", "mkisofs")
        self.assertEqual(self.output.read_bytes(), b"previous")
        self.assertEqual(list(self.directory.glob(".retrofile-pack-*")), [])

    def test_invalid_iso_rejected_and_command_has_no_boot_options(self):
        self.output.write_bytes(b"previous")
        with patch("src.folder_archive.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 0)) as run:
            with self.assertRaisesRegex(PackError, "valid data ISO"):
                pack_folder(str(self.source), str(self.output), "iso", "mkisofs")
        arguments = run.call_args.args[0]
        self.assertEqual(arguments[-1], str(self.source.resolve()))
        self.assertNotIn("-b", arguments)
        self.assertNotIn("-eltorito-alt-boot", arguments)
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertEqual(self.output.read_bytes(), b"previous")

    def test_missing_executable_reports_error_and_cleans_partial_output(self):
        with patch("src.folder_archive.subprocess.run", side_effect=FileNotFoundError("missing mkisofs")):
            with self.assertRaisesRegex(PackError, "missing mkisofs"):
                pack_folder(str(self.source), str(self.output), "iso", "mkisofs")
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.directory.glob(".retrofile-pack-*")), [])

    @unittest.skipIf(os.name == "nt", "Requires POSIX named pipes")
    def test_special_entries_are_explicit_errors(self):
        os.mkfifo(self.source / "pipe")
        with self.assertRaisesRegex(PackError, "special filesystem entry"):
            pack_folder(str(self.source), str(self.output), "zip")
        self.assertFalse(self.output.exists())

    def test_real_data_iso_contains_source_contents_without_boot_catalog(self):
        executable = find_mkisofs()
        if not executable:
            self.skipTest("mkisofs is not installed")
        pack_folder(str(self.source), str(self.output), "iso", executable)
        data = self.output.read_bytes()
        self.assertEqual(data[16 * 2048:16 * 2048 + 7], b"\x01CD001\x01")
        self.assertIn(b"archive contents", data)
        self.assertNotIn(b"EL TORITO SPECIFICATION", data)
        self.assertEqual((self.source / "nested/data.txt").read_bytes(), b"archive contents")


if __name__ == "__main__":
    unittest.main()
