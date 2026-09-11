from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QFileInfo, QPoint, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygon
from PySide6.QtWidgets import QFileIconProvider

_ICON_CACHE_DIR = Path.home() / ".wfcache" / "ui_icons"


def _branch_arrow_pixmap(open_state: bool, color: str = "#3f3f3f") -> QPixmap:
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    if open_state:
        points = [QPoint(4, 6), QPoint(12, 6), QPoint(8, 11)]
    else:
        points = [QPoint(6, 4), QPoint(6, 12), QPoint(11, 8)]
    painter.drawPolygon(QPolygon(points))
    painter.end()

    return pixmap


def _ensure_branch_arrow_icons() -> tuple[str, str, str, str]:
    _ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    closed_path = _ICON_CACHE_DIR / "branch_closed.png"
    open_path = _ICON_CACHE_DIR / "branch_open.png"
    closed_white_path = _ICON_CACHE_DIR / "branch_closed_white.png"
    open_white_path = _ICON_CACHE_DIR / "branch_open_white.png"
    if not closed_path.exists():
        _branch_arrow_pixmap(False).save(str(closed_path))
    if not open_path.exists():
        _branch_arrow_pixmap(True).save(str(open_path))
    if not closed_white_path.exists():
        _branch_arrow_pixmap(False, "#ffffff").save(str(closed_white_path))
    if not open_white_path.exists():
        _branch_arrow_pixmap(True, "#ffffff").save(str(open_white_path))
    return str(closed_path), str(open_path), str(closed_white_path), str(open_white_path)


class XPIconProvider(QFileIconProvider):
    def __init__(self) -> None:
        super().__init__()
        self._native_icon_provider = QFileIconProvider()
        self._folder_icon = self._build_folder_icon()
        self._file_icon = self._build_file_icon()

    def icon(self, info_or_type):  # type: ignore[override]
        if isinstance(info_or_type, QFileInfo):
            if info_or_type.isDir():
                if sys.platform == "darwin" and info_or_type.filePath().lower().endswith(".app"):
                    native_icon = self._native_icon_provider.icon(info_or_type)
                    if not native_icon.isNull():
                        return native_icon
                return self._folder_icon
            return self._file_icon

        if info_or_type == QFileIconProvider.Folder:
            return self._folder_icon
        if info_or_type == QFileIconProvider.File:
            return self._file_icon
        return super().icon(info_or_type)

    @staticmethod
    def _build_folder_icon() -> QIcon:
        pixmap = QPixmap(18, 16)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, False)
        # Push toward a brighter yellow palette with a softer hairline edge.
        painter.setPen(QPen(QColor("#c7a347"), 0))
        painter.setBrush(QColor("#ffe689"))
        painter.drawRect(1, 5, 16, 10)
        painter.setBrush(QColor("#fff5cc"))
        painter.drawRect(2, 2, 7, 4)
        painter.end()

        return QIcon(pixmap)

    @staticmethod
    def _build_file_icon() -> QIcon:
        pixmap = QPixmap(14, 16)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setPen(QPen(QColor("#7b7b7b"), 1))
        painter.setBrush(QColor("#fffef8"))
        painter.drawRect(1, 1, 11, 14)
        painter.setPen(QPen(QColor("#d8d8d8"), 1))
        painter.drawLine(3, 5, 10, 5)
        painter.drawLine(3, 8, 10, 8)
        painter.drawLine(3, 11, 10, 11)
        painter.end()

        return QIcon(pixmap)


def xp_stylesheet() -> str:
    closed_arrow_path, open_arrow_path, closed_arrow_white_path, open_arrow_white_path = (
        _ensure_branch_arrow_icons()
    )
    return """
QMainWindow {
    background-color: #f6f0dc;
}

QToolBar {
    background: #efe7cb;
    border: 1px solid #8a867a;
    spacing: 4px;
    padding: 3px;
}

QToolBar::separator {
    width: 1px;
    background: #8a867a;
    margin: 2px 4px;
}

QStatusBar {
    background: #efe7cb;
    border-top: 1px solid #8a867a;
    color: #1f1f1f;
}

QTreeView {
    background: #fffdf3;
    border: 1px solid #b8ad8a;
    alternate-background-color: #fbf7e8;
    color: #000000;
    gridline-color: #d6d6d6;
}

QTreeView::item:selected:active,
QTreeWidget::item:selected:active,
QTreeView::branch:selected:active {
    background: #316ac5;
    color: #ffffff;
}

QTreeView::item:selected:!active,
QTreeWidget::item:selected:!active,
QTreeView::branch:selected:!active {
    background: #d7e2f2;
    color: #1f3358;
}

QTreeView::branch:has-children:closed {
    image: url(__CLOSED_ARROW_PATH__);
}

QTreeView::branch:has-children:open {
    image: url(__OPEN_ARROW_PATH__);
}

QTreeView::branch:has-children:closed:selected:active {
    image: url(__CLOSED_ARROW_WHITE_PATH__);
}

QTreeView::branch:has-children:open:selected:active {
    image: url(__OPEN_ARROW_WHITE_PATH__);
}

QListWidget {
    background: #fffdf3;
    border: 1px solid #b8ad8a;
    color: #000000;
}

QListWidget::item:selected:active {
    background: #316ac5;
    color: #ffffff;
}

QListWidget::item:selected:!active {
    background: #d7e2f2;
    color: #1f3358;
}

QHeaderView::section {
    background: #d4d0c8;
    border: 1px solid #9b9b9b;
    padding: 3px 6px;
    color: #000000;
    font-weight: bold;
}

QLineEdit {
    background: #fffdf3;
    border: 1px solid #b8ad8a;
    padding: 3px 4px;
    color: #000000;
}

QMenu {
    background-color: #ffffff;
    border: 1px solid #8a867a;
}

QMenu::item {
    padding: 4px 20px;
    background: transparent;
}

QMenu::item:selected {
    background: #316ac5;
    color: #ffffff;
}

QDialog {
    background: #efe7cb;
}

QPushButton {
    min-width: 74px;
    padding: 3px 12px;
    background: #d4d0c8;
    border: 1px solid #7f7f7f;
}

QPushButton:focus {
    border: 1px solid #0a246a;
}

QPushButton:pressed {
    background: #c5c1b9;
}
""".replace("__CLOSED_ARROW_PATH__", closed_arrow_path).replace(
        "__OPEN_ARROW_PATH__", open_arrow_path
    ).replace(
        "__CLOSED_ARROW_WHITE_PATH__", closed_arrow_white_path
    ).replace(
        "__OPEN_ARROW_WHITE_PATH__", open_arrow_white_path
    )
