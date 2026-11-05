from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QCheckBox

from src.dialogs import PackFolderDialog
from src.file_explorer import ExplorerWindow, _PackWorker
from src.folder_archive import PackError


class PackFolderUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self):
        window = MagicMock()
        window._pack_worker = None
        return window

    def test_dialog_formats_extensions_and_no_bootable_checkbox(self):
        dialog = PackFolderDialog("/source/folder")
        self.assertEqual(dialog.format_combo.count(), 4)
        for key, suffix in (("zip", ".zip"), ("gzip", ".tar.gz"), ("tar", ".tar"), ("iso", ".iso")):
            dialog.format_combo.setCurrentIndex(dialog.format_combo.findData(key))
            self.assertEqual(dialog.archive_format(), key)
            self.assertEqual(Path(dialog.destination()).name, "folder" + suffix)
        self.assertIn("non-bootable", dialog.description.text())
        self.assertEqual(dialog.findChildren(QCheckBox), [])

    def test_dialog_validates_source_and_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            dialog = PackFolderDialog(str(source))
            dialog.destination_edit.setText(str(source / "bad.zip"))
            with patch("src.dialogs.QMessageBox.warning") as warning:
                dialog.accept()
            warning.assert_called_once()
            self.assertNotEqual(dialog.result(), QDialog.Accepted)
            dialog.destination_edit.setText(str(Path(directory) / "archive"))
            dialog.accept()
            self.assertEqual(dialog.result(), QDialog.Accepted)
            self.assertTrue(dialog.destination().endswith("archive.zip"))

    def test_existing_destination_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            output = Path(directory) / "archive.zip"
            output.write_bytes(b"existing")
            for answer in (QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes):
                with self.subTest(answer=answer):
                    dialog = PackFolderDialog(str(source))
                    dialog.destination_edit.setText(str(output))
                    with patch("src.dialogs.QMessageBox.question", return_value=answer):
                        dialog.accept()
                    self.assertEqual(dialog.result() == QDialog.Accepted,
                                     answer == QMessageBox.StandardButton.Yes)

    def test_menu_target_and_busy_state(self):
        window = self.window()
        menu = MagicMock()
        ExplorerWindow._add_pack_folder_action(window, menu, "/folder")
        self.assertEqual(menu.addAction.call_args.args[0], "Pack Folder...")
        menu.addAction.call_args.args[1]()
        window._pack_folder.assert_called_once_with("/folder")
        menu.addAction.return_value.setEnabled.assert_called_once_with(True)
        window._pack_worker = object()
        ExplorerWindow._add_pack_folder_action(window, menu, "/folder")
        menu.addAction.return_value.setEnabled.assert_called_with(False)

    def test_report_and_tree_context_menus_offer_packing(self):
        window = self.window()
        window.drive_report.table.itemAt.return_value.row.return_value = 0
        window.drive_report.drive_entries = [("USB", "/usb")]
        with patch("src.file_explorer.QMenu"):
            ExplorerWindow._show_drive_report_context_menu(window, QPoint())
        self.assertEqual(window._add_pack_folder_action.call_args.args[1], "/usb")
        self.assertEqual(window._add_unmount_drive_action.call_args.args[1], "/usb")
        window.this_pc_view.itemAt.return_value.data.return_value = "/folder"
        with patch("src.file_explorer.QMenu"):
            ExplorerWindow._show_file_system_context_menu(window, QPoint())
        self.assertEqual(window._add_pack_folder_action.call_args.args[1], "/folder")
        self.assertEqual(window._add_unmount_drive_action.call_args.args[1], "/folder")

    def test_main_context_offers_pack_for_folder_not_file(self):
        for folder in (False, True):
            with self.subTest(folder=folder):
                window = self.window()
                window.current_path.return_value = "/target"
                with patch("src.file_explorer.QMenu"), \
                        patch("src.file_explorer.os.path.isdir", return_value=folder):
                    ExplorerWindow._show_context_menu(window, QPoint())
                self.assertEqual(window._add_pack_folder_action.called, folder)
                if folder:
                    self.assertEqual(window._add_pack_folder_action.call_args.args[1], "/target")

    def test_cancel_does_not_start_worker(self):
        window = self.window()
        with patch("src.file_explorer.PackFolderDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.Rejected
            ExplorerWindow._pack_folder(window, "/folder")
        window._pack_thread_pool.start.assert_not_called()

    def test_zip_does_not_require_mkisofs_and_runs_in_background(self):
        window = self.window()
        with patch("src.file_explorer.PackFolderDialog") as dialog, \
                patch("src.file_explorer.find_mkisofs") as find, \
                patch("src.file_explorer.QProgressDialog") as progress:
            dialog.return_value.exec.return_value = QDialog.Accepted
            dialog.return_value.archive_format.return_value = "zip"
            dialog.return_value.destination.return_value = "/output.zip"
            ExplorerWindow._pack_folder(window, "/folder")
        find.assert_not_called()
        self.assertEqual(window._pack_worker.archive_format, "zip")
        self.assertIsNone(window._pack_worker.executable)
        window._pack_thread_pool.start.assert_called_once_with(window._pack_worker)
        progress.return_value.show.assert_called_once()

    def test_iso_missing_tool_picker_can_be_cancelled(self):
        window = self.window()
        window._settings.value.return_value = ""
        with patch("src.file_explorer.PackFolderDialog") as dialog, \
                patch("src.file_explorer.find_mkisofs", return_value=None), \
                patch("src.file_explorer.QFileDialog.getOpenFileName", return_value=("", "")), \
                patch("src.file_explorer.QMessageBox.information"):
            dialog.return_value.exec.return_value = QDialog.Accepted
            dialog.return_value.archive_format.return_value = "iso"
            ExplorerWindow._pack_folder(window, "/folder")
        window._pack_thread_pool.start.assert_not_called()

    def test_worker_emits_success_or_error(self):
        for error in (None, PackError("failed")):
            with self.subTest(error=error):
                worker = _PackWorker("/folder", "/output.zip", "zip", None)
                completed = MagicMock()
                worker.signals.completed.connect(completed)
                with patch("src.file_explorer.pack_folder", return_value="/output.zip", side_effect=error):
                    worker.run()
                completed.assert_called_once_with("failed" if error else "/output.zip", error is None)

    def test_completion_clears_state_and_reports_errors(self):
        for success in (False, True):
            window = self.window()
            progress = window._pack_progress
            with patch("src.file_explorer.QMessageBox.warning") as warning, \
                    patch("src.file_explorer.QMessageBox.information") as information:
                ExplorerWindow._on_pack_completed(window, "result", success)
            self.assertEqual(warning.called, not success)
            self.assertEqual(information.called, success)
            self.assertEqual(window.refresh.called, success)
            progress.close.assert_called_once()
            self.assertIsNone(window._pack_worker)
            self.assertIsNone(window._pack_progress)

    def test_close_blocked_while_packing(self):
        window = self.window()
        window._pack_worker = object()
        event = MagicMock()
        with patch("src.file_explorer.QMessageBox.information"):
            ExplorerWindow.closeEvent(window, event)
        event.ignore.assert_called_once()


if __name__ == "__main__":
    unittest.main()
