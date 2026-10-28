import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication, QDialog, QListWidget, QListWidgetItem, QMenu, QMessageBox, QTreeView,
)

from src.airs_vfs.drive import DriveError
from src.dialogs import CustomToolsDialog
from src.file_explorer import ExplorerWindow, _VfsUnmountWorker


class FileExplorerOpenTests(unittest.TestCase):
    def test_custom_tool_passes_target_as_one_argument(self):
        for target in ("/work/current folder", "/work/file name;echo.txt"):
            with self.subTest(target=target):
                with patch("src.file_explorer.subprocess.Popen") as popen:
                    ExplorerWindow._run_custom_tool(
                        MagicMock(), '"/Applications/My Tool/bin/tool" --mode "read only"',
                        target, "/work",
                    )
                self.assertEqual(popen.call_args.args[0], [
                    "/Applications/My Tool/bin/tool", "--mode", "read only", target,
                ])
                self.assertEqual(popen.call_args.kwargs["cwd"], "/work")
                self.assertNotIn("shell", popen.call_args.kwargs)

    def test_custom_tool_reports_invalid_or_missing_command(self):
        for command, error in (("", None), ("missing-tool", FileNotFoundError("not found"))):
            with self.subTest(command=command):
                with patch("src.file_explorer.subprocess.Popen", side_effect=error), \
                        patch("src.file_explorer.QMessageBox.warning") as warning:
                    ExplorerWindow._run_custom_tool(MagicMock(), command, "/work", "/work")
                warning.assert_called_once()

    def test_custom_tool_reports_unclosed_posix_quote(self):
        with patch("src.file_explorer.sys.platform", "darwin"), \
                patch("src.file_explorer.subprocess.Popen") as popen, \
                patch("src.file_explorer.QMessageBox.warning") as warning:
            ExplorerWindow._run_custom_tool(MagicMock(), '"unclosed', "/work", "/work")
        popen.assert_not_called()
        warning.assert_called_once()

    def test_custom_tool_preserves_windows_paths(self):
        with patch("src.file_explorer.sys.platform", "win32"), \
                patch("src.file_explorer.subprocess.Popen") as popen:
            ExplorerWindow._run_custom_tool(
                MagicMock(), r'"C:\Program Files\Editor\editor.exe" --reuse-window',
                r"C:\Work\file name.txt", r"C:\Work",
            )
        self.assertEqual(popen.call_args.args[0], [
            r"C:\Program Files\Editor\editor.exe", "--reuse-window", r"C:\Work\file name.txt",
        ])

    def test_custom_tools_settings_ignore_invalid_entries(self):
        window = MagicMock()
        for stored, expected in (
            ('[{"name":"Editor","command":"code"}]', [{"name": "Editor", "command": "code"}]),
            ('[{"name":"Broken"}, null, {"name":1,"command":"code"}]', []),
            ('{"name":"wrong shape"}', []), ("invalid json", []),
        ):
            with self.subTest(stored=stored):
                window._settings.value.return_value = stored
                self.assertEqual(ExplorerWindow._custom_tools(window), expected)

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


class CustomToolsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_add_edit_remove_and_validate(self):
        dialog = CustomToolsDialog([{"name": "Editor", "command": "code"}])
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.table.columnCount(), 2)
        dialog._add_row("Viewer", "open")
        dialog.table.item(0, 1).setText("code --reuse-window")
        dialog.table.selectRow(1)
        dialog._remove_rows()
        self.assertEqual(dialog.tools(), [{"name": "Editor", "command": "code --reuse-window"}])
        dialog._add_row("Incomplete", "")
        with patch("src.dialogs.QMessageBox.warning") as warning:
            dialog.accept()
        warning.assert_called_once()
        self.assertNotEqual(dialog.result(), QDialog.Accepted)
        dialog.table.item(1, 1).setText("viewer")
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.Accepted)

    def test_menu_lists_tools_above_configure_and_captures_target(self):
        window = MagicMock()
        window._showing_this_pc = False
        window.current_path.return_value = "/current"
        window._custom_tools.return_value = [
            {"name": "Editor", "command": "code"}, {"name": "Viewer", "command": "open"},
        ]
        menu = QMenu()
        ExplorerWindow._add_custom_tools_menu(window, menu, "/current/my file.txt")
        more = menu.actions()[0].menu()
        self.assertEqual(more.title(), "More...")
        self.assertEqual([action.text() for action in more.actions()],
                         ["Editor", "Viewer", "", "Configure..."])
        for action, command in zip(more.actions(), ("code", "open")):
            action.trigger()
            window._run_custom_tool.assert_called_with(command, "/current/my file.txt", "/current")
        more.actions()[-1].trigger()
        window._configure_custom_tools.assert_called_once()

    def test_context_target_uses_clicked_item_or_current_folder_in_both_views(self):
        for thumbnails in (False, True):
            with self.subTest(thumbnails=thumbnails):
                window = MagicMock()
                window.current_path.return_value = "/current"
                paths = ["/current/folder", "/current/file name.txt"]
                if thumbnails:
                    view = QListWidget()
                    for path in paths:
                        item = QListWidgetItem(path, view)
                        item.setData(Qt.ItemDataRole.UserRole, path)
                    view.setCurrentRow(0)
                else:
                    view = QTreeView()
                    model = QStandardItemModel()
                    for path in paths:
                        model.appendRow(QStandardItem(path))
                    view.setModel(model)
                    view.setCurrentIndex(model.index(0, 0))
                    window.fs_model.filePath.side_effect = lambda index: paths[index.row()]
                view.resize(500, 300)
                view.show()
                self.app.processEvents()
                window.sender.return_value = view
                for row, expected in ((0, paths[0]), (1, paths[1]), (None, "/current")):
                    if row is None:
                        pos = QPoint(10, view.viewport().height() - 10)
                    elif thumbnails:
                        pos = view.visualItemRect(view.item(row)).center()
                    else:
                        pos = view.visualRect(model.index(row, 0)).center()
                    with patch("src.file_explorer.QMenu"):
                        ExplorerWindow._show_context_menu(window, pos)
                    self.assertEqual(window._add_custom_tools_menu.call_args.args[1], expected)
                view.close()

    def test_configure_persists_tools_and_cancel_leaves_them_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = str(Path(directory) / "settings.ini")
            window = MagicMock()
            window._settings = QSettings(settings_path, QSettings.Format.IniFormat)
            tools = [{"name": "Editor", "command": "code"}]
            with patch("src.file_explorer.CustomToolsDialog") as dialog_class:
                dialog = dialog_class.return_value
                dialog.exec.return_value = QDialog.Accepted
                dialog.tools.return_value = tools
                ExplorerWindow._configure_custom_tools(window)
                window._settings.sync()
                restored = MagicMock()
                restored._settings = QSettings(settings_path, QSettings.Format.IniFormat)
                self.assertEqual(ExplorerWindow._custom_tools(restored), tools)
                dialog.exec.return_value = QDialog.Rejected
                dialog.tools.return_value = []
                ExplorerWindow._configure_custom_tools(window)
                self.assertEqual(ExplorerWindow._custom_tools(window), tools)


if __name__ == "__main__":
    unittest.main()