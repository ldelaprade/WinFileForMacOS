import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QMessageBox

from src.airs_vfs.drive import DriveError
from src.file_explorer import ExplorerWindow, _VfsUnmountWorker


class FileExplorerOpenTests(unittest.TestCase):
    def test_unsubst_confirmation_choices(self):
        for answer in (QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No,
                       QMessageBox.StandardButton.Cancel):
            with self.subTest(answer=answer):
                window = MagicMock()
                window._vfs_drives = {"S": "target"}
                with patch("src.file_explorer.QMessageBox.question", return_value=answer):
                    ExplorerWindow._unsubst_drive(window, "S", "target")
                if answer == QMessageBox.StandardButton.Cancel:
                    window._start_unsubst.assert_not_called()
                    window.show_this_pc.assert_not_called()
                    self.assertIn("S", window._vfs_drives)
                else:
                    window._start_unsubst.assert_called_once_with(
                        "S", "target", answer == QMessageBox.StandardButton.Yes,
                    )
                    window.show_this_pc.assert_not_called()
                    self.assertIn("S", window._vfs_drives)

    def test_unsubst_failure_keeps_drive_state(self):
        window = MagicMock()
        window._vfs_drives = {"S": "target"}
        progress = window._unsubst_progress
        with patch("src.file_explorer.QMessageBox.warning") as warning:
            ExplorerWindow._on_unsubst_completed(window, "denied", False)
        warning.assert_called_once()
        progress.close.assert_called_once()
        self.assertIsNone(window._unsubst_worker)
        self.assertIsNone(window._unsubst_progress)
        window.show_this_pc.assert_not_called()
        self.assertIn("S", window._vfs_drives)

    def test_unsubst_success_closes_progress_and_refreshes_drives(self):
        window = MagicMock()
        window._vfs_drives = {"S": "target"}
        window._unsubst_worker.drive_letter = "S"
        progress = window._unsubst_progress
        ExplorerWindow._on_unsubst_completed(window, "S", True)
        progress.close.assert_called_once()
        self.assertIsNone(window._unsubst_worker)
        self.assertIsNone(window._unsubst_progress)
        self.assertNotIn("S", window._vfs_drives)
        window.show_this_pc.assert_called_once()

    def test_unsubst_shows_busy_dialog_before_starting_worker(self):
        for delete_contents in (False, True):
            with self.subTest(delete_contents=delete_contents):
                window = MagicMock()
                window._vfs_worker = None
                window._unsubst_worker = None
                with patch("src.file_explorer.QProgressDialog") as dialog:
                    progress = dialog.return_value
                    window._vfs_thread_pool.start.side_effect = lambda worker: progress.show.assert_called_once()
                    ExplorerWindow._start_unsubst(window, "S", "target", delete_contents)
                self.assertEqual(dialog.call_args.args[2:4], (0, 0))
                self.assertEqual("Deleting contents" in dialog.call_args.args[0], delete_contents)
                progress.setCancelButton.assert_called_once_with(None)
                self.assertIs(window._unsubst_progress, progress)
                self.assertEqual(window._unsubst_worker.delete_contents, delete_contents)
                window._vfs_thread_pool.start.assert_called_once_with(window._unsubst_worker)

    def test_unsubst_worker_emits_completion_for_both_modes_and_errors(self):
        for delete_contents in (False, True):
            for error in (None, DriveError("denied"), OSError("unavailable")):
                with self.subTest(delete_contents=delete_contents, error=error):
                    worker = _VfsUnmountWorker("S", "target", delete_contents)
                    completed = MagicMock()
                    worker.signals.completed.connect(completed)
                    with patch("src.file_explorer.unmount_drive", side_effect=error) as unmount:
                        worker.run()
                    unmount.assert_called_once_with(
                        "S", delete_contents=delete_contents, expected_target="target",
                    )
                    completed.assert_called_once_with("S" if error is None else str(error), error is None)

    def test_unsubst_does_not_start_during_another_vfs_operation(self):
        for mounting in (False, True):
            window = MagicMock()
            window._vfs_worker = object() if mounting else None
            window._unsubst_worker = None if mounting else object()
            with patch("src.file_explorer.QMessageBox.information") as information, \
                    patch("src.file_explorer.QProgressDialog") as dialog:
                ExplorerWindow._start_unsubst(window, "S", "target", False)
            information.assert_called_once()
            dialog.assert_not_called()
            window._vfs_thread_pool.start.assert_not_called()

    def test_unsubst_menu_only_appears_for_mapped_drive_roots(self):
        window = MagicMock()
        window.drive_report.substituted_drives = {"S": "target"}
        for path, expected in (("S:/", True), ("s:\\", True), ("C:/", False),
                               ("S:/nested", False)):
            with self.subTest(path=path):
                menu = MagicMock()
                ExplorerWindow._add_unsubst_action(window, menu, path)
                self.assertEqual(menu.addAction.called, expected)
                if expected:
                    self.assertEqual(menu.addAction.call_args.args[0], "Unsubst...")
                    menu.addAction.call_args.args[1]()
                    window._unsubst_drive.assert_called_with("S", "target")

    @unittest.skipIf(os.name == "nt", "POSIX execute permissions are required")
    def test_open_launches_executable_file(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "tool"
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o755)

            custom_library_path = str(Path(directory) / "custom-libraries")
            with patch.dict(
                os.environ,
                {
                    "LD_LIBRARY_PATH": os.pathsep.join(
                        ("/snap/core20/current/lib/x86_64-linux-gnu", custom_library_path)
                    )
                },
            ):
                with (
                    patch("src.file_explorer.subprocess.Popen") as popen,
                    patch("src.file_explorer.QDesktopServices.openUrl") as open_url,
                ):
                    ExplorerWindow._open_file(object(), str(executable))

            popen.assert_called_once()
            self.assertEqual(popen.call_args.args[0], [str(executable)])
            self.assertEqual(popen.call_args.kwargs["cwd"], directory)
            self.assertEqual(
                popen.call_args.kwargs["env"]["LD_LIBRARY_PATH"], custom_library_path
            )
            open_url.assert_not_called()

    def test_open_uses_desktop_association_for_non_executable_file(self):
        with tempfile.TemporaryDirectory() as directory:
            document = Path(directory) / "notes.txt"
            document.write_text("notes\n")

            with (
                patch("src.file_explorer.subprocess.Popen") as popen,
                patch("src.file_explorer.QDesktopServices.openUrl") as open_url,
            ):
                ExplorerWindow._open_file(object(), str(document))

            popen.assert_not_called()
            self.assertEqual(open_url.call_args.args[0].toLocalFile(), str(document))

    def test_windows_user_folder_path_reads_registry_value(self):
        import sys

        registry = MagicMock()
        registry.HKEY_CURRENT_USER = object()
        registry.OpenKey.return_value.__enter__.return_value = object()
        registry.QueryValueEx.return_value = (r"%USERPROFILE%\Redirected", 2)

        with (
            patch("src.file_explorer.sys.platform", "win32"),
            patch.dict(sys.modules, {"winreg": registry}),
            patch("src.file_explorer.os.path.expandvars", return_value=r"C:\Users\Me\Redirected"),
        ):
            path = ExplorerWindow._user_folder_path("Desktop")

        self.assertEqual(path, r"C:\Users\Me\Redirected")
        registry.OpenKey.assert_called_once_with(
            registry.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        )
        registry.QueryValueEx.assert_called_once_with(
            registry.OpenKey.return_value.__enter__.return_value,
            "Desktop",
        )

    def test_windows_download_path_uses_downloads_known_folder_value(self):
        import sys

        registry = MagicMock()
        registry.HKEY_CURRENT_USER = object()
        registry.OpenKey.return_value.__enter__.return_value = object()
        registry.QueryValueEx.return_value = (r"C:\Users\Me\Cloud\Downloads", 2)

        with (
            patch("src.file_explorer.sys.platform", "win32"),
            patch.dict(sys.modules, {"winreg": registry}),
        ):
            path = ExplorerWindow._user_folder_path("Downloads")

        self.assertEqual(path, r"C:\Users\Me\Cloud\Downloads")
        registry.QueryValueEx.assert_called_once_with(
            registry.OpenKey.return_value.__enter__.return_value,
            "{374DE290-123F-4565-9164-39C4925E467B}",
        )


if __name__ == "__main__":
    unittest.main()