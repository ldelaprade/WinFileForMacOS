from __future__ import annotations

from PySide6.QtCore import QFileInfo, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFileIconProvider


class XPIconProvider(QFileIconProvider):
    def __init__(self) -> None:
        super().__init__()
        self._folder_icon = self._build_folder_icon()
        self._file_icon = self._build_file_icon()

    def icon(self, info_or_type):  # type: ignore[override]
        if isinstance(info_or_type, QFileInfo):
            if info_or_type.isDir():
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
        painter.setPen(QPen(QColor("#9a7b2f"), 1))
        painter.setBrush(QColor("#f6d66f"))
        painter.drawRect(1, 5, 16, 10)
        painter.setBrush(QColor("#f9e08e"))
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
    selection-background-color: #316ac5;
    selection-color: #ffffff;
    gridline-color: #d6d6d6;
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
"""
