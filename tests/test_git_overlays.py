import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QApplication, QListView, QStyledItemDelegate, QStyleOptionViewItem

from src.git_operations import GitIconStatus
from src.git_overlays import GitOverlayController, GitOverlayDelegate, _StatusWorker, paint_git_badge


class GitOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = QListView()
        self.addCleanup(self.view.deleteLater)
        self.directory = os.path.abspath("/repository")
        self.controller = GitOverlayController(lambda: self.directory, self.view)
        self.controller._timer.stop()

    def test_badges_render_distinct_pixels(self):
        images = []
        for status in ("clean", "modified", "added", "deleted", "conflicted", "untracked", "ignored"):
            image = QImage(32, 32, QImage.Format_ARGB32)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            paint_git_badge(painter, QRectF(0, 0, 32, 32), status)
            painter.end()
            self.assertGreater(image.pixelColor(16, 16).alpha(), 0)
            self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
            self.assertFalse(any(image == previous for previous in images))
            images.append(image)

    def test_delegate_preserves_base_icon_and_only_overlays_decoration(self):
        model = QStandardItemModel(self.view)
        item = QStandardItem("tracked.txt")
        item.setData(os.path.join(self.directory, "tracked.txt"), Qt.UserRole)
        base = QPixmap(32, 32)
        base.fill(QColor("#ffff00"))
        item.setIcon(QIcon(base))
        model.appendRow(item)
        self.view.setModel(model)
        self.controller.snapshot = GitIconStatus(self.directory, {"tracked.txt": "modified"})
        delegate = GitOverlayDelegate(self.controller, self.view)
        self.assertEqual(delegate.status_for_index(model.index(0, 0)), "modified")
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 180, 40)
        option.decorationSize = base.size()
        images = []
        for renderer in (QStyledItemDelegate(self.view), delegate):
            image = QImage(180, 40, QImage.Format_ARGB32)
            image.fill(Qt.white)
            painter = QPainter(image)
            renderer.paint(painter, option, model.index(0, 0))
            painter.end()
            images.append(image)
        self.assertNotEqual(images[0], images[1])
        self.assertEqual(images[0].copy(50, 0, 130, 40), images[1].copy(50, 0, 130, 40))
        self.assertEqual(item.icon().pixmap(32, 32).toImage(), base.toImage())

    def test_navigation_clears_snapshot_and_discards_stale_results(self):
        self.controller.snapshot = GitIconStatus(self.directory, {"file": "clean"})
        self.controller._directory = self.directory
        self.controller._worker = object()
        previous = self.directory
        self.directory = os.path.abspath("/elsewhere")
        self.controller.refresh()
        self.assertIsNone(self.controller.snapshot)
        with patch.object(self.controller, "refresh") as refresh:
            self.controller._completed(previous, GitIconStatus(previous, {"file": "modified"}))
            refresh.assert_called_once()
        self.assertIsNone(self.controller.snapshot)

    def test_empty_directory_does_not_schedule_git(self):
        self.directory = ""
        with patch("src.git_overlays.QThreadPool.globalInstance") as pool:
            self.controller.refresh()
        pool.assert_not_called()

    def test_missing_git_or_non_repository_silently_clears_badges(self):
        worker = _StatusWorker(self.directory)
        results = []
        worker.signals.completed.connect(lambda directory, snapshot: results.append((directory, snapshot)))
        with patch("src.git_operations.GitOperations.icon_status", side_effect=OSError("No Git")):
            worker.run()
        self.assertEqual(results, [(self.directory, None)])

    def test_explorer_import_does_not_require_git_executable(self):
        environment = dict(os.environ, GIT_PYTHON_GIT_EXECUTABLE="/nonexistent/git-executable")
        result = subprocess.run(
            [sys.executable, "-c", "import src.file_explorer"],
            cwd=Path(__file__).resolve().parents[1], env=environment,
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)