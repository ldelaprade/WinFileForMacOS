from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import QDir, QFileInfo, QModelIndex, QPoint, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QDropEvent,
    QIcon,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileIconProvider,
    QFileSystemModel,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QToolBar,
    QTreeView,
    QVBoxLayout,
)


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


def _xp_stylesheet() -> str:
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


class DeleteConfirmDialog(QDialog):
    def __init__(self, message: str, parent: QMainWindow | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Delete")
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint)
        self.setModal(True)

        layout = QVBoxLayout(self)
        text_label = QLabel(message, self)
        text_label.setWordWrap(True)
        layout.addWidget(text_label)

        self.button_box = QDialogButtonBox(self)
        self.yes_button = self.button_box.addButton("Yes", QDialogButtonBox.AcceptRole)
        self.no_button = self.button_box.addButton("No", QDialogButtonBox.RejectRole)

        self.no_button.setDefault(True)
        self.no_button.setAutoDefault(True)
        self.no_button.setFocus()
        self.yes_button.setAutoDefault(True)

        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            focused = self.focusWidget()
            if focused is self.yes_button:
                self.accept()
                return
            if focused is self.no_button:
                self.reject()
                return
        super().keyPressEvent(event)


class ActionConfirmDialog(QDialog):
    def __init__(
        self,
        title: str,
        message: str,
        yes_label: str = "Yes",
        no_label: str = "No",
        parent: QMainWindow | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(message, self))

        self.button_box = QDialogButtonBox(self)
        self.yes_button = self.button_box.addButton(yes_label, QDialogButtonBox.AcceptRole)
        self.no_button = self.button_box.addButton(no_label, QDialogButtonBox.RejectRole)

        self.no_button.setDefault(True)
        self.no_button.setAutoDefault(True)
        self.no_button.setFocus()
        self.yes_button.setAutoDefault(True)

        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            focused = self.focusWidget()
            if focused is self.yes_button:
                self.accept()
                return
            if focused is self.no_button:
                self.reject()
                return
        super().keyPressEvent(event)


class ConfirmingDropTreeView(QTreeView):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._confirm_move_callback = None

    def set_move_confirm_callback(self, callback) -> None:
        self._confirm_move_callback = callback

    def dropEvent(self, event: QDropEvent) -> None:
        is_move_drop = event.proposedAction() == Qt.MoveAction or event.dropAction() == Qt.MoveAction
        destination_path = self._resolve_drop_destination_path(event)
        source_paths = self._resolve_drag_source_paths(event)
        if (
            is_move_drop
            and event.source() is not None
            and self._confirm_move_callback is not None
            and not self._confirm_move_callback(destination_path, source_paths)
        ):
            event.ignore()
            return
        super().dropEvent(event)

    def _resolve_drop_destination_path(self, event: QDropEvent) -> str:
        if hasattr(event, "position"):
            drop_pos = event.position().toPoint()
        else:
            drop_pos = event.pos()

        target_index = self.indexAt(drop_pos)
        if not target_index.isValid():
            target_index = self.rootIndex()

        model = self.model()
        if isinstance(model, QFileSystemModel) and target_index.isValid():
            target_path = model.filePath(target_index)
            if target_path and os.path.isfile(target_path):
                return os.path.dirname(target_path)
            return target_path
        return ""

    def _resolve_drag_source_paths(self, event: QDropEvent) -> list[str]:
        urls = event.mimeData().urls()
        local_paths = [url.toLocalFile() for url in urls if url.isLocalFile()]
        if local_paths:
            return local_paths

        source_view = event.source()
        if isinstance(source_view, QTreeView):
            selection_model = source_view.selectionModel()
            model = source_view.model()
            if selection_model is not None and isinstance(model, QFileSystemModel):
                selected_rows = selection_model.selectedRows()
                return [model.filePath(index) for index in selected_rows if index.isValid()]
        return []


class ExplorerWindow(QMainWindow):
    _PREVIEWABLE_IMAGE_EXTENSIONS = {
        ".bmp",
        ".gif",
        ".heic",
        ".jpeg",
        ".jpg",
        ".png",
        ".tif",
        ".tiff",
        ".webp",
    }
    _PREVIEWABLE_DOCUMENT_EXTENSIONS = {
        ".pdf",
    }
    _PREVIEWABLE_VIDEO_EXTENSIONS = {
        ".avi",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".webm",
    }

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("WinFile XP for Mac OS")
        self.resize(1200, 760)

        self._history: list[str] = []
        self._history_pos = -1
        self._clipboard_paths: list[str] = []
        self._clipboard_mode: str | None = None
        self._view_mode: str = "list"  # "list" or "thumbnail"
        self._thumbnail_icon_cache: dict[tuple[str, int, int, int, int], QIcon] = {}

        self._setup_models()
        self._setup_views()
        self._setup_toolbar()
        self._setup_statusbar()
        self._setup_shortcuts()

        self.navigate_to(str(Path.home()), record_history=True)

    def _setup_models(self) -> None:
        icon_provider = XPIconProvider()

        self.fs_model = QFileSystemModel(self)
        self.fs_model.setReadOnly(False)
        self.fs_model.setFilter(
            QDir.AllEntries | QDir.NoDotAndDotDot | QDir.AllDirs | QDir.Files
        )
        self.fs_model.setIconProvider(icon_provider)
        self.fs_model.setRootPath("")
        self.dir_model = QFileSystemModel(self)
        self.dir_model.setReadOnly(False)
        self.dir_model.setFilter(QDir.AllDirs | QDir.NoDotAndDotDot)
        self.dir_model.setIconProvider(icon_provider)
        self.dir_model.setRootPath("")

    def _setup_views(self) -> None:
        self.splitter = QSplitter(self)
        self.setCentralWidget(self.splitter)

        self.tree_view = ConfirmingDropTreeView(self.splitter)
        self.tree_view.setModel(self.dir_model)
        self.tree_view.setHeaderHidden(True)
        self.tree_view.setColumnHidden(1, True)
        self.tree_view.setColumnHidden(2, True)
        self.tree_view.setColumnHidden(3, True)
        self.tree_view.setAcceptDrops(True)
        self.tree_view.setDropIndicatorShown(True)
        self.tree_view.setDragDropMode(QTreeView.DropOnly)
        self.tree_view.setDefaultDropAction(Qt.MoveAction)
        self.tree_view.set_move_confirm_callback(self._confirm_drag_move)
        self.tree_view.clicked.connect(self._on_tree_clicked)

        self.list_view = ConfirmingDropTreeView(self.splitter)
        self.list_view.setModel(self.fs_model)
        self.list_view.setRootIsDecorated(False)
        self.list_view.setAlternatingRowColors(True)
        self.list_view.setSelectionBehavior(QTreeView.SelectRows)
        self.list_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list_view.setDragEnabled(True)
        self.list_view.setAcceptDrops(True)
        self.list_view.setDropIndicatorShown(True)
        self.list_view.setDragDropMode(QTreeView.DragDrop)
        self.list_view.setDragDropOverwriteMode(False)
        self.list_view.setDefaultDropAction(Qt.MoveAction)
        self.list_view.set_move_confirm_callback(self._confirm_drag_move)
        self.list_view.setSortingEnabled(True)
        self.list_view.sortByColumn(0, Qt.AscendingOrder)
        self.list_view.doubleClicked.connect(self._on_list_double_clicked)
        self.list_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_view.customContextMenuRequested.connect(self._show_context_menu)
        self.list_view.selectionModel().selectionChanged.connect(
            lambda *_: self._update_status()
        )

        self.thumbnail_view = QListWidget(self.splitter)
        self.thumbnail_view.setViewMode(QListWidget.IconMode)
        self.thumbnail_view.setResizeMode(QListWidget.Adjust)
        self.thumbnail_view.setIconSize(QSize(192, 192))
        self.thumbnail_view.setGridSize(QSize(236, 256))
        self.thumbnail_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.thumbnail_view.setDragEnabled(True)
        self.thumbnail_view.setAcceptDrops(True)
        self.thumbnail_view.setDropIndicatorShown(True)
        self.thumbnail_view.setDragDropMode(QListWidget.DragDrop)
        self.thumbnail_view.doubleClicked.connect(self._on_thumbnail_double_clicked)
        self.thumbnail_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.thumbnail_view.customContextMenuRequested.connect(self._show_context_menu)
        self.thumbnail_view.selectionModel().selectionChanged.connect(
            lambda *_: self._update_status()
        )
        self.thumbnail_view.hide()

        self.splitter.setSizes([300, 900, 0])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 1)

    def _setup_toolbar(self) -> None:
        toolbar = QToolBar("Navigation", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.back_action = QAction("Back", self)
        self.back_action.triggered.connect(self.go_back)
        toolbar.addAction(self.back_action)

        self.forward_action = QAction("Forward", self)
        self.forward_action.triggered.connect(self.go_forward)
        toolbar.addAction(self.forward_action)

        self.up_action = QAction("Up", self)
        self.up_action.triggered.connect(self.go_up)
        toolbar.addAction(self.up_action)

        toolbar.addSeparator()

        self.address_bar = QLineEdit(self)
        self.address_bar.setPlaceholderText("Path")
        self.address_bar.returnPressed.connect(self._on_address_enter)
        toolbar.addWidget(self.address_bar)

        self.go_action = QAction("Go", self)
        self.go_action.triggered.connect(self._on_address_enter)
        toolbar.addAction(self.go_action)

        self.refresh_action = QAction("Refresh", self)
        self.refresh_action.triggered.connect(self.refresh)
        toolbar.addAction(self.refresh_action)

        toolbar.addSeparator()

        self.view_toggle_action = QAction("Thumbnails", self)
        self.view_toggle_action.setCheckable(True)
        self.view_toggle_action.triggered.connect(self.toggle_view_mode)
        toolbar.addAction(self.view_toggle_action)

    def _setup_statusbar(self) -> None:
        self.status = QStatusBar(self)
        self.setStatusBar(self.status)
        self._update_status()

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key_F2), self, activated=self.rename_selected)
        QShortcut(QKeySequence(Qt.Key_Delete), self, activated=self.delete_selected)
        QShortcut(QKeySequence(Qt.Key_F5), self, activated=self.refresh)
        QShortcut(
            QKeySequence.StandardKey.Refresh,
            self,
            activated=self.refresh,
        )
        QShortcut(QKeySequence("Alt+Left"), self, activated=self.go_back)
        QShortcut(QKeySequence("Alt+Right"), self, activated=self.go_forward)
        QShortcut(QKeySequence("Alt+Up"), self, activated=self.go_up)
        QShortcut(QKeySequence(Qt.Key_Backspace), self, activated=self.go_up)

        select_all_shortcut = QShortcut(QKeySequence.StandardKey.SelectAll, self.list_view)
        select_all_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        select_all_shortcut.activated.connect(self.list_view.selectAll)

        open_return_shortcut = QShortcut(QKeySequence(Qt.Key_Return), self.list_view)
        open_return_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        open_return_shortcut.activated.connect(self.open_selected)

        open_enter_shortcut = QShortcut(QKeySequence(Qt.Key_Enter), self.list_view)
        open_enter_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        open_enter_shortcut.activated.connect(self.open_selected)

        QShortcut(QKeySequence.StandardKey.Copy, self, activated=self.copy_selected)
        QShortcut(QKeySequence.StandardKey.Cut, self, activated=self.cut_selected)
        QShortcut(QKeySequence.StandardKey.Paste, self, activated=self.paste_into_current)

        QShortcut(QKeySequence("Ctrl+T"), self, activated=self.open_terminal)

        QShortcut(QKeySequence("Alt+D"), self, activated=self.focus_address_bar)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.focus_address_bar)

    def _on_tree_clicked(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        path = self.dir_model.filePath(index)
        self.navigate_to(path, record_history=True)

    def _on_list_double_clicked(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        path = self.fs_model.filePath(index)
        if os.path.isdir(path):
            self.navigate_to(path, record_history=True)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _on_thumbnail_double_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if not path:
            return
        if os.path.isdir(path):
            self.navigate_to(path, record_history=True)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _confirm_drag_move(self, destination_path: str, source_paths: list[str]) -> bool:
        message = self._build_move_confirmation_message(destination_path, source_paths)
        dialog = ActionConfirmDialog(
            title="Move",
            message=message,
            yes_label="Move",
            no_label="No",
            parent=self,
        )
        return dialog.exec() == QDialog.Accepted

    @staticmethod
    def _selection_preview(paths: list[str], max_items: int = 3) -> str:
        names = [Path(path).name or path for path in paths]
        if not names:
            return "(no items)"

        if len(names) <= max_items:
            return "\n".join(f"- {name}" for name in names)

        shown = "\n".join(f"- {name}" for name in names[:max_items])
        remaining = len(names) - max_items
        return f"{shown}\n- ... and {remaining} more"

    def _build_move_confirmation_message(self, destination_path: str, source_paths: list[str]) -> str:
        item_count = len(source_paths)
        if item_count == 0:
            return "Move selected item(s) to this folder?"

        destination_name = Path(destination_path).name if destination_path else "target folder"
        preview = self._selection_preview(source_paths)
        if item_count == 1:
            return (
                "Move this item?\n\n"
                f"{preview}\n\n"
                f"Destination: {destination_name}"
            )

        return (
            f"Move {item_count} items?\n\n"
            f"{preview}\n\n"
            f"Destination: {destination_name}"
        )

    def _build_delete_confirmation_message(self, paths: list[str]) -> str:
        item_count = len(paths)
        preview = self._selection_preview(paths)
        if item_count == 1:
            return f"Delete this item permanently?\n\n{preview}"
        return f"Delete {item_count} items permanently?\n\n{preview}"

    def _show_context_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction("Open", self.open_selected)
        menu.addAction("Rename", self.rename_selected)
        menu.addAction("Delete", self.delete_selected)
        menu.addSeparator()
        menu.addAction("New Folder", self.new_folder)
        menu.addAction("Open Terminal", self.open_terminal)
        menu.addAction("Refresh", self.refresh)
        menu.exec(self.list_view.viewport().mapToGlobal(pos))

    def current_path(self) -> str:
        root_index = self.list_view.rootIndex()
        if not root_index.isValid():
            return str(Path.home())
        return self.fs_model.filePath(root_index)

    def navigate_to(self, path: str, record_history: bool = False) -> None:
        normalized = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(normalized):
            QMessageBox.warning(self, "Invalid path", f"Folder not found:\n{normalized}")
            return

        root_index = self.fs_model.index(normalized)
        tree_index = self.dir_model.index(normalized)
        self.tree_view.setCurrentIndex(tree_index)
        self.tree_view.scrollTo(tree_index)
        self.list_view.setRootIndex(root_index)
        self.address_bar.setText(normalized)

        self._populate_thumbnail_view(normalized)

        if record_history:
            if self._history_pos < len(self._history) - 1:
                self._history = self._history[: self._history_pos + 1]
            if not self._history or self._history[-1] != normalized:
                self._history.append(normalized)
                self._history_pos = len(self._history) - 1
        self._update_nav_actions()
        self._update_status()

    def _on_address_enter(self) -> None:
        self.navigate_to(self.address_bar.text().strip(), record_history=True)

    def focus_address_bar(self) -> None:
        self.address_bar.setFocus()
        self.address_bar.selectAll()

    def go_back(self) -> None:
        if self._history_pos <= 0:
            return
        self._history_pos -= 1
        self.navigate_to(self._history[self._history_pos], record_history=False)

    def go_forward(self) -> None:
        if self._history_pos >= len(self._history) - 1:
            return
        self._history_pos += 1
        self.navigate_to(self._history[self._history_pos], record_history=False)

    def go_up(self) -> None:
        current = Path(self.current_path())
        parent = current.parent
        if parent == current:
            return
        self.navigate_to(str(parent), record_history=True)

    def _update_nav_actions(self) -> None:
        self.back_action.setEnabled(self._history_pos > 0)
        self.forward_action.setEnabled(self._history_pos < len(self._history) - 1)

    def selected_indexes(self) -> list[QModelIndex]:
        if self._view_mode == "thumbnail":
            return []
        selection = self.list_view.selectionModel().selectedRows()
        return [index for index in selection if index.isValid()]

    def selected_paths(self) -> list[str]:
        if self._view_mode == "thumbnail":
            selected_items = self.thumbnail_view.selectedItems()
            return [item.data(Qt.UserRole) for item in selected_items if item.data(Qt.UserRole)]
        return [self.fs_model.filePath(index) for index in self.selected_indexes()]

    def open_selected(self) -> None:
        paths = self.selected_paths()
        if not paths:
            return
        path = paths[0]
        if os.path.isdir(path):
            self.navigate_to(path, record_history=True)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def copy_selected(self) -> None:
        paths = self.selected_paths()
        if not paths:
            return
        self._clipboard_paths = paths
        self._clipboard_mode = "copy"
        self.status.showMessage(f"Copied {len(paths)} item(s)", 2500)

    def cut_selected(self) -> None:
        paths = self.selected_paths()
        if not paths:
            return
        self._clipboard_paths = paths
        self._clipboard_mode = "cut"
        self.status.showMessage(f"Cut {len(paths)} item(s)", 2500)

    def paste_into_current(self) -> None:
        if not self._clipboard_paths or self._clipboard_mode is None:
            return

        destination_dir = Path(self.current_path())
        if not destination_dir.is_dir():
            return

        failures: list[str] = []
        moved_count = 0
        copied_count = 0

        for source_path_str in list(self._clipboard_paths):
            source_path = Path(source_path_str)
            if not source_path.exists():
                failures.append(f"{source_path}: not found")
                continue

            target_path = destination_dir / source_path.name
            if source_path.parent == destination_dir:
                continue

            if target_path.exists():
                failures.append(f"{target_path}: destination already exists")
                continue

            try:
                if self._clipboard_mode == "copy":
                    if source_path.is_dir():
                        shutil.copytree(source_path, target_path)
                    else:
                        shutil.copy2(source_path, target_path)
                    copied_count += 1
                elif self._clipboard_mode == "cut":
                    shutil.move(str(source_path), str(target_path))
                    moved_count += 1
            except OSError as error:
                failures.append(f"{source_path} -> {target_path}: {error}")

        if self._clipboard_mode == "cut" and moved_count > 0:
            self._clipboard_paths = [
                p for p in self._clipboard_paths if Path(p).exists()
            ]
            if not self._clipboard_paths:
                self._clipboard_mode = None

        self.refresh()

        if failures:
            QMessageBox.warning(
                self,
                "Paste",
                "Some items could not be pasted:\n" + "\n".join(failures),
            )

        if copied_count > 0:
            self.status.showMessage(f"Pasted {copied_count} copied item(s)", 2500)
        elif moved_count > 0:
            self.status.showMessage(f"Moved {moved_count} item(s)", 2500)

    def rename_selected(self) -> None:
        paths = self.selected_paths()
        if len(paths) != 1:
            return

        source = Path(paths[0])
        new_name, ok = QInputDialog.getText(self, "Rename", "New name:", text=source.name)
        if not ok or not new_name.strip():
            return

        destination = source.with_name(new_name.strip())
        if destination.exists():
            QMessageBox.warning(self, "Rename", "An item with this name already exists.")
            return

        try:
            source.rename(destination)
            self.refresh()
        except OSError as error:
            QMessageBox.critical(self, "Rename failed", str(error))

    def delete_selected(self) -> None:
        paths = self.selected_paths()
        if not paths:
            return

        dialog = DeleteConfirmDialog(self._build_delete_confirmation_message(paths), self)
        if dialog.exec() != QDialog.Accepted:
            return

        failures: list[str] = []
        for path in paths:
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            except OSError as error:
                failures.append(f"{path}: {error}")

        self.refresh()
        if failures:
            QMessageBox.warning(self, "Delete", "Some items could not be deleted:\n" + "\n".join(failures))

    def new_folder(self) -> None:
        parent = Path(self.current_path())
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:", text="New Folder")
        if not ok or not name.strip():
            return
        destination = parent / name.strip()
        try:
            destination.mkdir(parents=False, exist_ok=False)
            self.refresh()
        except OSError as error:
            QMessageBox.critical(self, "New Folder failed", str(error))

    def open_terminal(self) -> None:
        current_path = self.current_path()
        try:
            os.system(f'open -a Terminal.app "{current_path}"')
        except OSError as error:
            QMessageBox.critical(self, "Open Terminal failed", str(error))

    def refresh(self) -> None:
        current = self.current_path()
        self.fs_model.setRootPath("")
        self.dir_model.setRootPath("")
        index = self.fs_model.index(current)
        tree_index = self.dir_model.index(current)
        self.list_view.setRootIndex(index)
        self.tree_view.setCurrentIndex(tree_index)
        self.tree_view.scrollTo(tree_index)
        self._populate_thumbnail_view(current)
        self._update_status()

    def _update_status(self) -> None:
        if self._view_mode == "thumbnail":
            item_count = self.thumbnail_view.count()
        else:
            root_index = self.list_view.rootIndex()
            if not root_index.isValid():
                self.status.showMessage("Ready")
                return
            item_count = self.fs_model.rowCount(root_index)

        selected_paths = self.selected_paths()

        selected_size = 0
        for path in selected_paths:
            if os.path.isfile(path):
                try:
                    selected_size += os.path.getsize(path)
                except OSError:
                    continue

        selected_info = f"Selected: {len(selected_paths)}"
        if selected_size:
            selected_info += f" ({self._format_size(selected_size)})"

        self.status.showMessage(f"Items: {item_count} | {selected_info}")

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(size_bytes)
        for unit in units:
            if size < 1024.0 or unit == units[-1]:
                if unit == "B":
                    return f"{int(size)} {unit}"
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size_bytes} B"

    def _populate_thumbnail_view(self, path: str) -> None:
        self.thumbnail_view.clear()
        try:
            for entry in os.listdir(path):
                entry_path = os.path.join(path, entry)
                if entry.startswith('.'):
                    continue

                item = QListWidgetItem()
                item.setText(entry)
                item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
                item.setIcon(self._thumbnail_icon_for_path(entry_path))

                item.setData(Qt.UserRole, entry_path)
                self.thumbnail_view.addItem(item)
        except OSError:
            pass

    def _thumbnail_icon_for_path(self, path: str) -> QIcon:
        if os.path.isdir(path):
            return self.fs_model.iconProvider().icon(QFileIconProvider.Folder)

        icon_size = self.thumbnail_view.iconSize()
        cache_key = self._thumbnail_cache_key(path, icon_size)
        if cache_key is not None:
            cached_icon = self._thumbnail_icon_cache.get(cache_key)
            if cached_icon is not None:
                return cached_icon

        suffix = Path(path).suffix.lower()
        preview = QPixmap()
        if suffix in self._PREVIEWABLE_IMAGE_EXTENSIONS:
            preview = QPixmap(path)
        elif (
            suffix in self._PREVIEWABLE_DOCUMENT_EXTENSIONS
            or suffix in self._PREVIEWABLE_VIDEO_EXTENSIONS
        ):
            preview = self._quicklook_preview_pixmap(path, icon_size)

        if not preview.isNull():
            icon = self._icon_from_preview_pixmap(preview, icon_size)
        else:
            icon = self.fs_model.iconProvider().icon(QFileIconProvider.File)

        if cache_key is not None:
            if len(self._thumbnail_icon_cache) > 500:
                self._thumbnail_icon_cache.clear()
            self._thumbnail_icon_cache[cache_key] = icon

        return icon

    def _thumbnail_cache_key(self, path: str, icon_size: QSize) -> tuple[str, int, int, int, int] | None:
        try:
            stat = os.stat(path)
            return (
                path,
                stat.st_mtime_ns,
                stat.st_size,
                icon_size.width(),
                icon_size.height(),
            )
        except OSError:
            return None

    @staticmethod
    def _icon_from_preview_pixmap(preview: QPixmap, icon_size: QSize) -> QIcon:
        scaled = preview.scaled(
            icon_size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        canvas = QPixmap(icon_size)
        canvas.fill(Qt.transparent)

        painter = QPainter(canvas)
        x = (icon_size.width() - scaled.width()) // 2
        y = (icon_size.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        painter.end()
        return QIcon(canvas)

    @staticmethod
    def _quicklook_preview_pixmap(path: str, icon_size: QSize) -> QPixmap:
        preview_size = str(max(icon_size.width(), icon_size.height(), 256))
        try:
            with tempfile.TemporaryDirectory(prefix="winfile-preview-") as temp_dir:
                subprocess.run(
                    ["qlmanage", "-t", "-s", preview_size, "-o", temp_dir, path],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

                preview_files = sorted(
                    Path(temp_dir).glob("*.png"),
                    key=lambda candidate: candidate.stat().st_mtime_ns,
                    reverse=True,
                )

                for preview_file in preview_files:
                    pixmap = QPixmap(str(preview_file))
                    if not pixmap.isNull():
                        return pixmap
        except OSError:
            pass

        return QPixmap()

    def toggle_view_mode(self) -> None:
        if self._view_mode == "list":
            self._view_mode = "thumbnail"
            self.list_view.hide()
            self.thumbnail_view.show()
            self._ensure_thumbnail_width()
            self.view_toggle_action.setChecked(True)
        else:
            self._view_mode = "list"
            self.thumbnail_view.hide()
            self.list_view.show()
            self._ensure_list_width()
            self.view_toggle_action.setChecked(False)
        self._update_status()

    def _ensure_thumbnail_width(self) -> None:
        def apply_sizes() -> None:
            total = max(1, self.splitter.width())
            tree = max(220, int(total * 0.25))
            content = max(300, total - tree)
            self.splitter.setSizes([tree, 0, content])

        QTimer.singleShot(0, apply_sizes)

    def _ensure_list_width(self) -> None:
        def apply_sizes() -> None:
            total = max(1, self.splitter.width())
            tree = max(220, int(total * 0.25))
            content = max(300, total - tree)
            self.splitter.setSizes([tree, content, 0])

        QTimer.singleShot(0, apply_sizes)


def run() -> None:
    app = QApplication([])
    app.setApplicationName("WinFile")
    app.setStyle("Fusion")
    app.setStyleSheet(_xp_stylesheet())
    window = ExplorerWindow()
    window.show()
    app.exec()
