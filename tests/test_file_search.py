import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QEventLoop, QPoint, Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QListWidget, QListWidgetItem, QWidget

from src.file_explorer import ExplorerWindow
from src.file_search import FileSearchDialog, _SearchWorker


class FileSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_search_recursive_case_insensitive_wildcards_and_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            nested = root / "nested"
            nested.mkdir(parents=True)
            (root / "top.AVI").touch()
            (nested / "movie1.avi").touch()
            (nested / "movie2.txt").touch()
            outside = Path(directory) / "outside"
            outside.mkdir()
            (outside / "linked.avi").touch()
            try:
                (root / "link").symlink_to(outside, target_is_directory=True)
            except OSError:
                pass
            worker = _SearchWorker(str(root), "*.avi")
            batches = []
            completed = MagicMock()
            worker.signals.results.connect(batches.extend)
            worker.signals.completed.connect(completed)
            worker.run()
            self.assertEqual(set(batches), {str(root / "top.AVI"), str(nested / "movie1.avi")})
            completed.assert_called_once_with([], False)

    def test_cancel_and_errors_are_reported(self):
        worker = _SearchWorker("/missing", "*")
        completed = MagicMock()
        worker.signals.completed.connect(completed)
        worker.run()
        self.assertTrue(completed.call_args.args[0])
        self.assertFalse(completed.call_args.args[1])
        worker = _SearchWorker("/missing", "*")
        worker.cancelled.set()
        completed = MagicMock()
        worker.signals.completed.connect(completed)
        worker.run()
        self.assertTrue(completed.call_args.args[1])

    def test_dialog_displays_search_errors(self):
        parent = QWidget()
        dialog = FileSearchDialog("/folder", parent, MagicMock(), MagicMock())
        dialog.show()
        with patch("src.file_search.QMessageBox.warning") as warning:
            dialog._search_completed(["access denied"], False)
        self.assertIn("access denied", warning.call_args.args[2])
        dialog.close()

    def test_stopping_active_search_keeps_ui_responsive(self):
        started = threading.Event()
        release = threading.Event()

        def walk(root, onerror):
            started.set()
            release.wait(5)
            yield root, [], ["movie.avi"]

        parent = QWidget()
        dialog = FileSearchDialog("/folder", parent, MagicMock(), MagicMock())
        dialog.pattern_edit.setText("*.avi")
        with patch("src.file_search.os.walk", side_effect=walk):
            dialog.start_search()
            worker = dialog.worker
            self.assertTrue(started.wait(5))
            loop = QEventLoop()
            worker.signals.completed.connect(loop.quit)
            dialog.stop_search()
            release.set()
            QTimer.singleShot(5000, loop.quit)
            loop.exec()
            self.assertIsNone(dialog.worker)
            self.assertTrue(dialog.status.text().startswith("Stopped:"))
            self.assertEqual(dialog.results.count(), 0)
        dialog.close()

    def test_dialog_search_and_result_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "nested").mkdir()
            movie = root / "nested" / "My Movie.AVI"
            movie.touch()
            parent = QWidget()
            open_file, open_folder = MagicMock(), MagicMock()
            dialog = FileSearchDialog(directory, parent, open_file, open_folder)
            dialog.pattern_edit.setText("movie")
            dialog.start_search()
            loop = QEventLoop()
            dialog.worker.signals.completed.connect(loop.quit)
            QTimer.singleShot(5000, loop.quit)
            loop.exec()
            self.assertIsNone(dialog.worker)
            self.assertEqual(dialog.results.count(), 1)
            item = dialog.results.item(0)
            dialog._open_result(item)
            open_file.assert_called_once_with(str(movie))
            dialog.results.setCurrentItem(item)
            dialog._open_containing_folder()
            open_folder.assert_called_once_with(str(movie.parent))
            self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_sidebar_menus_offer_search_for_clicked_folders(self):
        window = MagicMock()
        window._SYSTEM_TRASH_FAVORITE = "trash"
        window._ftp_shares = {}
        window.favorites_view.itemAt.return_value.data.return_value = "/favorite"
        window.this_pc_view.itemAt.return_value.data.return_value = "/machine"
        for method, expected in (
            (ExplorerWindow._show_favorites_context_menu, "/favorite"),
            (ExplorerWindow._show_file_system_context_menu, "/machine"),
        ):
            with self.subTest(path=expected), patch("src.file_explorer.QMenu") as menu:
                method(window, QPoint())
                actions = menu.return_value.addAction.call_args_list
                action = next(call for call in actions if call.args[0] == "Search Files...")
                action.args[1]()
                window._search_files.assert_called_with(expected)

    def test_copy_path_menu_targets_clicked_item(self):
        window = MagicMock()
        window.current_path.return_value = "/current"
        view = QListWidget()
        item = QListWidgetItem("file", view)
        item.setData(Qt.ItemDataRole.UserRole, "/current/file")
        view.show()
        self.app.processEvents()
        window.sender.return_value = view
        with patch("src.file_explorer.QMenu") as menu:
            ExplorerWindow._show_context_menu(window, view.visualItemRect(item).center())
            actions = menu.return_value.addAction.call_args_list
            action = next(call for call in actions if call.args[0] == "Copy file path")
            action.args[1]()
            window._copy_file_path.assert_called_once_with("/current/file")
        view.close()
