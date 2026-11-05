import os
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QDir, QEventLoop, QPoint, QSettings, Qt, QTimer
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileSystemModel, QListWidget, QListWidgetItem, QMenu, QMessageBox, QTreeView,
)

from src.airs_vfs.drive import DriveError
from src.dialogs import CustomToolsDialog
from src.file_explorer import ExplorerWindow, _DiskUnmountWorker, _VfsUnmountWorker


class FileExplorerOpenTests(unittest.TestCase):
    def test_listing_diagnostics_reports_names_without_changing_contents(self):
        from src.main import write_listing_diagnostics

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "folder"
            folder.mkdir()
            document = folder / "real.doc"
            document.write_bytes(b"keep this")
            output = Path(directory) / "report.json"
            with patch("src.main._loaded_windows_modules", return_value=[]):
                write_listing_diagnostics(str(folder), str(output))
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["python_entries"], ["real.doc"])
            self.assertEqual([entry["name"] for entry in report["qt_entries"]], ["real.doc"])
            self.assertEqual(document.read_bytes(), b"keep this")
            self.assertFalse(any(entry["exists"] for entry in report["reported_paths"]))

    def test_late_rows_in_current_folder_reapply_visibility(self):
        window = MagicMock()
        window._showing_this_pc = False
        window.current_path.return_value = "J:/"
        parent = window.list_view.rootIndex.return_value
        ExplorerWindow._on_file_rows_inserted(window, parent, 0, 1)
        window._apply_file_visibility.assert_called_once_with("J:/")
        window._apply_file_visibility.reset_mock()
        ExplorerWindow._on_file_rows_inserted(window, object(), 0, 1)
        window._apply_file_visibility.assert_not_called()

    def test_hidden_rows_are_excluded_from_file_operation_selection(self):
        window = MagicMock()
        window._view_mode = "details"
        visible, hidden = MagicMock(), MagicMock()
        visible.row.return_value = 0
        hidden.row.return_value = 1
        window.list_view.selectionModel.return_value.selectedRows.return_value = [visible, hidden]
        window.list_view.isRowHidden.side_effect = lambda row, _parent: row == 1
        self.assertEqual(ExplorerWindow.selected_indexes(window), [visible])

    def test_real_windows_files_are_not_hidden_by_name_and_case(self):
        window = MagicMock()
        window.fs_model.rowCount.return_value = 1
        window.fs_model.fileName.return_value = "ZZZZZ4270261263.doc"
        window._is_hidden_path.return_value = False
        entry = MagicMock()
        entry.name = "zzzzz4270261263.DOC"
        with patch("src.file_explorer.sys.platform", "win32"), \
                patch("src.file_explorer.is_network_path", return_value=False), \
                patch("src.file_explorer.os.scandir") as scan:
            scan.return_value.__enter__.return_value = [entry]
            ExplorerWindow._apply_file_visibility(window, "J:/")
        self.assertFalse(window.list_view.setRowHidden.call_args.args[2])

    def test_visibility_does_not_treat_failed_or_network_scans_as_empty(self):
        for network in (False, True):
            window = MagicMock()
            window.fs_model.rowCount.return_value = 1
            window._is_hidden_path.return_value = False
            with patch("src.file_explorer.sys.platform", "win32"), \
                    patch("src.file_explorer.is_network_path", return_value=network), \
                    patch("src.file_explorer.os.scandir", side_effect=PermissionError()) as scan:
                ExplorerWindow._apply_file_visibility(window, "J:/")
            self.assertFalse(window.list_view.setRowHidden.call_args.args[2])
            self.assertEqual(scan.called, not network)

    def test_windows_listing_hides_model_entries_absent_from_directory_enumeration(self):
        for show_hidden in (False, True):
            with self.subTest(show_hidden=show_hidden):
                window = MagicMock()
                window._show_hidden = show_hidden
                root = window.list_view.rootIndex.return_value
                names = ["real.doc", "ZZZZZ1802596832.doc", "ZZZZZ4270261263.doc"]
                window.fs_model.rowCount.return_value = len(names)
                window.fs_model.index.side_effect = lambda row, _column, _root: row
                window.fs_model.fileName.side_effect = lambda index: names[index]
                window.fs_model.filePath.side_effect = lambda index: "J:/" + names[index]
                window._is_hidden_path.return_value = False
                with patch("src.file_explorer.sys.platform", "win32"), \
                        patch("src.file_explorer.is_network_path", return_value=False), \
                        patch("src.file_explorer.os.scandir") as scan:
                    scan.return_value.__enter__.return_value = [MagicMock(name="entry")]
                    scan.return_value.__enter__.return_value[0].name = "real.doc"
                    ExplorerWindow._apply_file_visibility(window, "J:/")
                self.assertEqual(window.list_view.setRowHidden.call_args_list, [
                    unittest.mock.call(0, root, False),
                    unittest.mock.call(1, root, True),
                    unittest.mock.call(2, root, True),
                ])

    def test_navigation_to_reused_removable_root_removes_cached_files(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            document = Path(directory) / "old.doc"
            document.write_text("old contents")
            model = QFileSystemModel()
            model.setOption(QFileSystemModel.Option.DontWatchForChanges, True)
            model.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot)
            window = MagicMock()
            window.fs_model = model
            window._rescan_directory = lambda path: ExplorerWindow._rescan_directory(window, path)
            window._showing_this_pc = False
            window._is_app_bundle.return_value = False
            window._view_mode = "details"
            window.current_path.return_value = directory

            def wait_for_listing(action):
                loop = QEventLoop()
                loaded = []

                def on_loaded(path):
                    if os.path.normcase(os.path.realpath(path)) == os.path.normcase(os.path.realpath(directory)):
                        loaded.append(path)
                        loop.quit()

                timer = QTimer()
                timer.setSingleShot(True)
                timer.timeout.connect(loop.quit)
                model.directoryLoaded.connect(on_loaded)
                timer.start(2000)
                action()
                loop.exec()
                timer.stop()
                model.directoryLoaded.disconnect(on_loaded)
                self.assertTrue(loaded, "Directory was not reloaded")

            wait_for_listing(lambda: model.setRootPath(directory))
            self.assertEqual(model.rowCount(model.index(directory)), 1)
            document.unlink()
            with patch("src.file_explorer.sys.platform", "win32"), patch(
                "src.file_explorer.DriveReportWidget.is_removable_windows_volume", return_value=True
            ):
                wait_for_listing(lambda: ExplorerWindow._navigate_now(window, directory, False))
            self.assertEqual(model.rowCount(model.index(directory)), 0)

    def test_copy_paths_updates_internal_and_system_clipboards(self):
        paths = ["/work/notes.txt", "/work/Project Folder"]
        window = MagicMock()
        clipboard = MagicMock()

        with patch("src.file_explorer.QApplication.clipboard", return_value=clipboard):
            ExplorerWindow._copy_paths(window, paths)

        self.assertEqual(window._clipboard_paths, paths)
        self.assertEqual(window._clipboard_mode, "copy")
        mime_data = clipboard.setMimeData.call_args.args[0]
        self.assertEqual([url.toLocalFile() for url in mime_data.urls()], paths)
        self.assertEqual(mime_data.text(), "\n".join(paths))

    def test_application_trash_action_platform_and_bundle_gating(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "Example.app"
            app.mkdir()
            for platform, expected in (("darwin", True), ("linux", False), ("win32", False)):
                with self.subTest(platform=platform), patch("src.file_explorer.sys.platform", platform):
                    self.assertEqual(ExplorerWindow._is_app_bundle(str(app)), expected)
            with patch("src.file_explorer.sys.platform", "darwin"):
                self.assertFalse(ExplorerWindow._is_app_bundle(directory))

    def test_system_application_is_protected_without_stat(self):
        self.assertTrue(ExplorerWindow._application_is_protected("/System/Applications/Example.app"))

    def test_application_trash_menu_protected_and_unprotected(self):
        for protected in (False, True):
            window = MagicMock()
            window._is_app_bundle.return_value = True
            window._application_is_protected.return_value = protected
            menu = MagicMock()
            ExplorerWindow._add_application_trash_action(window, menu, "/Applications/Example.app")
            menu.addAction.return_value.setEnabled.assert_called_once_with(not protected)
            menu.addAction.call_args.args[1]()
            window._trash_application.assert_called_once_with("/Applications/Example.app")

    def test_application_trash_rejects_protected_or_missing_bundle(self):
        for missing in (False, True):
            window = MagicMock()
            window._is_app_bundle.return_value = not missing
            window._application_is_protected.return_value = not missing
            with patch("src.file_explorer.QMessageBox.warning") as warning, \
                    patch("src.file_explorer.move_items_to_trash") as trash:
                ExplorerWindow._trash_application(window, "/Applications/Example.app")
            warning.assert_called_once()
            trash.assert_not_called()

    def test_application_trash_confirmation_cancel_errors_and_success(self):
        path = "/Applications/Example.app"
        for accepted, failures in ((False, []), (True, []), (True, ["Permission denied"])):
            with self.subTest(accepted=accepted, failures=failures):
                window = MagicMock()
                window._is_app_bundle.return_value = True
                window._application_is_protected.return_value = False
                window._trash_mode_enabled = False
                with patch("src.file_explorer.DeleteConfirmDialog") as dialog, \
                        patch("src.file_explorer.move_items_to_trash", return_value=failures) as trash, \
                        patch("src.file_explorer.delete_items") as delete, \
                        patch("src.file_explorer.QMessageBox.warning") as warning:
                    dialog.return_value.exec.return_value = QDialog.Accepted if accepted else QDialog.Rejected
                    ExplorerWindow._trash_application(window, path)
                self.assertIn("user data will be retained", dialog.call_args.args[0])
                self.assertIn("vendor's uninstaller", dialog.call_args.args[0])
                delete.assert_not_called()
                if accepted:
                    trash.assert_called_once_with([path])
                    window.refresh.assert_called_once()
                else:
                    trash.assert_not_called()
                    window.refresh.assert_not_called()
                self.assertEqual(warning.called, bool(failures))
                self.assertEqual(window.status.showMessage.called, accepted and not failures)

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

    def test_unmount_menu_only_appears_for_removable_macos_volumes(self):
        path = "/Volumes/USB"
        for removable in (False, True):
            with self.subTest(removable=removable):
                menu = MagicMock()
                window = MagicMock()
                with patch("src.file_explorer.sys.platform", "darwin"), patch(
                    "src.file_explorer.DriveReportWidget.is_removable_macos_volume",
                    return_value=removable,
                ):
                    ExplorerWindow._add_unmount_drive_action(window, menu, path)
                self.assertEqual(menu.addAction.called, removable)
                if removable:
                    self.assertEqual(menu.addAction.call_args.args[0], "Unmount Drive")
                    menu.addAction.call_args.args[1]()
                    window._unmount_drive.assert_called_once_with(path)

    def test_linux_eject_menu_only_appears_for_removable_volumes(self):
        path = "/media/user/USB"
        for device in (None, "/dev/sdb"):
            with self.subTest(device=device):
                menu = MagicMock()
                window = MagicMock()
                with patch("src.file_explorer.sys.platform", "linux"), patch(
                    "src.file_explorer.DriveReportWidget.linux_removable_device",
                    return_value=device,
                ):
                    ExplorerWindow._add_unmount_drive_action(window, menu, path)
                self.assertEqual(menu.addAction.called, device is not None)
                if device:
                    self.assertEqual(menu.addAction.call_args.args[0], "Eject Drive")
                    menu.addAction.call_args.args[1]()
                    window._unmount_drive.assert_called_once_with(path)

    def test_disk_unmount_worker_runs_diskutil_and_reports_result(self):
        path = "/Volumes/USB Stick"
        worker = _DiskUnmountWorker(path)
        completed = MagicMock()
        worker.signals.completed.connect(completed)
        with patch("src.file_explorer.sys.platform", "darwin"), \
            patch("src.file_explorer.subprocess.run") as run:
            worker.run()
        run.assert_called_once_with(
            ["diskutil", "unmount", path],
            capture_output=True,
            check=True,
            text=True,
            timeout=15,
        )
        completed.assert_called_once_with(path, True)

    def test_linux_eject_worker_powers_off_removable_parent_disk(self):
        path = "/media/user/USB"
        worker = _DiskUnmountWorker(path)
        completed = MagicMock()
        worker.signals.completed.connect(completed)
        with patch("src.file_explorer.sys.platform", "linux"), patch(
            "src.file_explorer.DriveReportWidget.linux_removable_device_info",
            return_value=("/dev/sdb", ["/dev/sdc1"]),
        ), patch("src.file_explorer.subprocess.run") as run:
            worker.run()
        self.assertEqual(
            run.call_args_list,
            [
                unittest.mock.call(
                    ["udisksctl", "unmount", "--block-device", "/dev/sdc1"],
                    capture_output=True,
                    check=True,
                    text=True,
                    timeout=30,
                ),
                unittest.mock.call(
                    ["udisksctl", "power-off", "--block-device", "/dev/sdb"],
                    capture_output=True,
                    check=True,
                    text=True,
                    timeout=30,
                ),
            ],
        )
        completed.assert_called_once_with(path, True)

    def test_linux_eject_worker_refuses_unrecognized_device(self):
        worker = _DiskUnmountWorker("/media/user/USB")
        completed = MagicMock()
        worker.signals.completed.connect(completed)
        with patch("src.file_explorer.sys.platform", "linux"), patch(
            "src.file_explorer.DriveReportWidget.linux_removable_device_info", return_value=None
        ), patch("src.file_explorer.subprocess.run") as run:
            worker.run()
        run.assert_not_called()
        self.assertFalse(completed.call_args.args[1])
        self.assertIn("removable Linux drive", completed.call_args.args[0])

    def test_linux_eject_worker_does_not_power_off_when_unmount_fails(self):
        path = "/media/user/USB"
        worker = _DiskUnmountWorker(path)
        completed = MagicMock()
        worker.signals.completed.connect(completed)
        error = subprocess.CalledProcessError(1, "udisksctl", stderr="device is busy")
        with patch("src.file_explorer.sys.platform", "linux"), patch(
            "src.file_explorer.DriveReportWidget.linux_removable_device_info",
            return_value=("/dev/sdb", ["/dev/sdb1"]),
        ), patch("src.file_explorer.subprocess.run", side_effect=error) as run:
            worker.run()
        run.assert_called_once()
        completed.assert_called_once_with("device is busy", False)

    def test_disk_unmount_worker_reports_system_error(self):
        worker = _DiskUnmountWorker("/Volumes/USB")
        completed = MagicMock()
        worker.signals.completed.connect(completed)
        with patch("src.file_explorer.sys.platform", "darwin"), patch(
            "src.file_explorer.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "diskutil", stderr="resource busy"),
        ):
            worker.run()
        completed.assert_called_once_with("resource busy", False)

    def test_windows_eject_menu_targets_drive_and_disables_while_busy(self):
        for busy in (False, True):
            window = MagicMock()
            window._disk_unmount_worker = object() if busy else None
            menu = MagicMock()
            with patch("src.file_explorer.sys.platform", "win32"), patch(
                "src.file_explorer.DriveReportWidget.is_removable_windows_volume", return_value=True
            ):
                ExplorerWindow._add_unmount_drive_action(window, menu, "J:/")
            self.assertEqual(menu.addAction.call_args.args[0], "Eject Drive")
            menu.addAction.return_value.setEnabled.assert_called_once_with(not busy)
            menu.addAction.call_args.args[1]()
            window._unmount_drive.assert_called_once_with("J:/")

    def test_windows_eject_worker_reports_success_and_failure(self):
        for error in (None, OSError("drive is in use")):
            worker = _DiskUnmountWorker("J:/")
            completed = MagicMock()
            worker.signals.completed.connect(completed)
            with patch("src.file_explorer.sys.platform", "win32"), patch(
                "src.file_explorer.DriveReportWidget.eject_windows_volume", side_effect=error
            ) as eject, patch("src.file_explorer.subprocess.run") as run:
                worker.run()
            eject.assert_called_once_with("J:/")
            run.assert_not_called()
            completed.assert_called_once_with("drive is in use" if error else "J:/", error is None)

    def test_windows_eject_leaves_drive_before_starting_worker(self):
        window = MagicMock()
        window._disk_unmount_worker = None
        window.current_path.return_value = "J:/folder"
        with patch("src.file_explorer.sys.platform", "win32"):
            ExplorerWindow._unmount_drive(window, "J:/")
        window.navigate_to.assert_called_once_with(str(Path.home()), record_history=True)
        window._disk_unmount_thread_pool.start.assert_called_once_with(window._disk_unmount_worker)

    def test_eject_completion_refreshes_only_on_success(self):
        for success in (False, True):
            window = MagicMock()
            window._disk_unmount_worker = _DiskUnmountWorker("J:/")
            with patch("src.file_explorer.sys.platform", "win32"), \
                    patch("src.file_explorer.QMessageBox.warning") as warning:
                ExplorerWindow._on_disk_unmount_completed(window, "drive is in use", success)
            self.assertIsNone(window._disk_unmount_worker)
            self.assertEqual(window.refresh.called, success)
            self.assertEqual(warning.called, not success)
            if success:
                window.status.showMessage.assert_called_once_with("Ejected J:/", 3000)

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
                    if row is None:
                        window._add_application_trash_action.assert_not_called()
                    else:
                        self.assertEqual(
                            window._add_application_trash_action.call_args.args[1], expected,
                        )
                    window._add_application_trash_action.reset_mock()
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