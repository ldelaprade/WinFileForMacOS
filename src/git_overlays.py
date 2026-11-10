from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRectF, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFileSystemModel, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QToolTip,
)

if TYPE_CHECKING:
    from .git_operations import GitIconStatus


_BADGES = {
    "clean": ("#269447", "Clean"),
    "modified": ("#d63d32", "Modified"),
    "added": ("#267bc0", "Added (staged)"),
    "deleted": ("#666666", "Deleted"),
    "conflicted": ("#df8914", "Conflicted"),
    "untracked": ("#63788e", "Untracked"),
    "ignored": ("#aaaaaa", "Ignored"),
}


def git_repository_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 32):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(size / 16, size / 16)
        painter.setPen(QPen(QColor("#269447"), 1.8, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(4, 4, 4, 12)
        branch = QPainterPath()
        branch.moveTo(4, 10)
        branch.cubicTo(4, 7, 12, 10, 12, 4)
        painter.drawPath(branch)
        painter.setBrush(QColor("#ffffff"))
        for horizontal, vertical in ((4, 3), (4, 13), (12, 3)):
            painter.drawEllipse(QRectF(horizontal - 2, vertical - 2, 4, 4))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def paint_git_badge(painter: QPainter, bounds: QRectF, status: str) -> None:
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.translate(bounds.topLeft())
    painter.scale(bounds.width() / 16, bounds.height() / 16)
    painter.setPen(QPen(QColor("#ffffff"), 1.2))
    painter.setBrush(QColor(_BADGES[status][0]))
    painter.drawEllipse(QRectF(0.6, 0.6, 14.8, 14.8))
    painter.setPen(QPen(QColor("#ffffff"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    mark = QPainterPath()
    if status == "clean":
        mark.moveTo(4, 8)
        mark.lineTo(7, 11)
        mark.lineTo(12, 5)
    elif status in {"added", "deleted"}:
        mark.moveTo(4, 8)
        mark.lineTo(12, 8)
        if status == "added":
            mark.moveTo(8, 4)
            mark.lineTo(8, 12)
    elif status in {"conflicted", "ignored"}:
        mark.moveTo(5, 5)
        mark.lineTo(11, 11)
        if status == "conflicted":
            mark.moveTo(11, 5)
            mark.lineTo(5, 11)
    else:
        font = painter.font()
        font.setPixelSize(13)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(0, -0.5, 16, 16), Qt.AlignCenter,
                         "?" if status == "untracked" else "!")
    painter.drawPath(mark)
    painter.restore()


class GitOverlayDelegate(QStyledItemDelegate):
    def __init__(self, controller: GitOverlayController, view) -> None:
        super().__init__(view)
        self.controller = controller

    def status_for_index(self, index) -> str | None:
        if index.column() != 0 or self.controller.snapshot is None:
            return None
        model = index.model()
        path = model.filePath(index) if isinstance(model, QFileSystemModel) else index.data(Qt.UserRole)
        return self.controller.snapshot.status_for(path) if isinstance(path, str) else None

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        status = self.status_for_index(index)
        if status is None:
            return
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        if styled.icon.isNull():
            return
        style = styled.widget.style() if styled.widget else QApplication.style()
        decoration = style.subElementRect(QStyle.SE_ItemViewItemDecoration, styled, styled.widget)
        if decoration.isEmpty():
            return
        size = min(24, max(10, round(min(decoration.width(), decoration.height()) * 0.55)))
        badge = QRectF(decoration.left(), decoration.bottom() - size + 1, size, size)
        paint_git_badge(painter, badge, status)

    def helpEvent(self, event, view, option, index) -> bool:
        status = self.status_for_index(index)
        if status is None:
            return super().helpEvent(event, view, option, index)
        existing = index.data(Qt.ToolTipRole) or index.data(Qt.DisplayRole) or ""
        QToolTip.showText(event.globalPos(), f"{existing}\nGit: {_BADGES[status][1]}", view)
        return True


class _StatusSignals(QObject):
    completed = Signal(str, object)


class _StatusWorker(QRunnable):
    def __init__(self, directory: str) -> None:
        super().__init__()
        self.directory = directory
        self.signals = _StatusSignals()

    def run(self) -> None:
        try:
            from .git_operations import GitOperations

            snapshot = GitOperations(self.directory).icon_status()
        except Exception:
            snapshot = None
        self.signals.completed.emit(self.directory, snapshot)


class GitOverlayController(QObject):
    snapshot_changed = Signal(object)

    def __init__(self, current_directory: Callable[[], str], parent: QObject) -> None:
        super().__init__(parent)
        self.current_directory = current_directory
        self.snapshot: GitIconStatus | None = None
        self._directory = ""
        self._worker: _StatusWorker | None = None
        self._views = []
        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

    def attach(self, view) -> None:
        view.setItemDelegate(GitOverlayDelegate(self, view))
        self._views.append(view)

    def refresh(self) -> None:
        directory = self.current_directory()
        if directory != self._directory:
            self._directory = directory
            self.snapshot = None
            self._repaint()
        if not directory or self._worker is not None:
            return
        self._worker = _StatusWorker(directory)
        self._worker.signals.completed.connect(self._completed)
        QThreadPool.globalInstance().start(self._worker)

    def _completed(self, directory: str, snapshot: GitIconStatus | None) -> None:
        self._worker = None
        if directory != self.current_directory():
            self.refresh()
            return
        self.snapshot = snapshot
        self._repaint()

    def _repaint(self) -> None:
        self.snapshot_changed.emit(self.snapshot)
        for view in self._views:
            view.viewport().update()