from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QDir, QModelIndex, QPoint, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QDesktopServices,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileIconProvider,
    QFileSystemModel,
    QInputDialog,
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
)

from .dialogs import (
    ActionConfirmDialog,
    DeleteConfirmDialog,
    build_delete_confirmation_message,
    build_move_confirmation_message,
)
from .dragdrop_views import ConfirmingDropTreeView
from .file_operations import create_folder, delete_items, paste_items, rename_item
from .navigation_state import NavigationHistory
from .thumbnail_previews import ThumbnailPreviewProvider
from .ui_theme import XPIconProvider, xp_stylesheet


class ExplorerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("WinFile XP for Mac OS")
        self.resize(1200, 760)

        self.navigation_history = NavigationHistory()
        self._clipboard_paths: list[str] = []
        self._clipboard_mode: str | None = None
        self._view_mode: str = "list"  # "list" or "thumbnail"
        self.thumbnail_provider: ThumbnailPreviewProvider | None = None

        self._setup_models()
        self._setup_views()
        self._setup_toolbar()
        self._setup_statusbar()
        self._setup_shortcuts()

        self.navigate_to(str(Path.home()), record_history=True)

    def _setup_models(self) -> None:
        icon_provider = XPIconProvider()
        self.thumbnail_provider = ThumbnailPreviewProvider(icon_provider)

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
        message = build_move_confirmation_message(destination_path, source_paths)
        dialog = ActionConfirmDialog(
            title="Move",
            message=message,
            yes_label="Move",
            no_label="No",
            parent=self,
        )
        return dialog.exec() == QDialog.Accepted

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
            self.navigation_history.record(normalized)
        self._update_nav_actions()
        self._update_status()

    def _on_address_enter(self) -> None:
        self.navigate_to(self.address_bar.text().strip(), record_history=True)

    def focus_address_bar(self) -> None:
        self.address_bar.setFocus()
        self.address_bar.selectAll()

    def go_back(self) -> None:
        target = self.navigation_history.go_back()
        if target is None:
            return
        self.navigate_to(target, record_history=False)

    def go_forward(self) -> None:
        target = self.navigation_history.go_forward()
        if target is None:
            return
        self.navigate_to(target, record_history=False)

    def go_up(self) -> None:
        current = Path(self.current_path())
        parent = current.parent
        if parent == current:
            return
        self.navigate_to(str(parent), record_history=True)

    def _update_nav_actions(self) -> None:
        self.back_action.setEnabled(self.navigation_history.can_go_back())
        self.forward_action.setEnabled(self.navigation_history.can_go_forward())

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

        result = paste_items(self._clipboard_paths, self._clipboard_mode, destination_dir)
        self._clipboard_paths = result.clipboard_paths
        self._clipboard_mode = result.clipboard_mode

        self.refresh()

        if result.failures:
            QMessageBox.warning(
                self,
                "Paste",
                "Some items could not be pasted:\n" + "\n".join(result.failures),
            )

        if result.copied_count > 0:
            self.status.showMessage(f"Pasted {result.copied_count} copied item(s)", 2500)
        elif result.moved_count > 0:
            self.status.showMessage(f"Moved {result.moved_count} item(s)", 2500)

    def rename_selected(self) -> None:
        paths = self.selected_paths()
        if len(paths) != 1:
            return

        source = Path(paths[0])
        new_name, ok = QInputDialog.getText(self, "Rename", "New name:", text=source.name)
        if not ok or not new_name.strip():
            return
        try:
            rename_item(source, new_name.strip())
            self.refresh()
        except FileExistsError:
            QMessageBox.warning(self, "Rename", "An item with this name already exists.")
        except OSError as error:
            QMessageBox.critical(self, "Rename failed", str(error))

    def delete_selected(self) -> None:
        paths = self.selected_paths()
        if not paths:
            return

        dialog = DeleteConfirmDialog(build_delete_confirmation_message(paths), self)
        if dialog.exec() != QDialog.Accepted:
            return

        failures = delete_items(paths)

        self.refresh()
        if failures:
            QMessageBox.warning(self, "Delete", "Some items could not be deleted:\n" + "\n".join(failures))

    def new_folder(self) -> None:
        parent = Path(self.current_path())
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:", text="New Folder")
        if not ok or not name.strip():
            return
        try:
            create_folder(parent, name.strip())
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
                if self.thumbnail_provider is not None:
                    item.setIcon(
                        self.thumbnail_provider.icon_for_path(
                            entry_path,
                            self.thumbnail_view.iconSize(),
                        )
                    )
                else:
                    item.setIcon(self.fs_model.iconProvider().icon(QFileIconProvider.File))

                item.setData(Qt.UserRole, entry_path)
                self.thumbnail_view.addItem(item)
        except OSError:
            pass

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
    app.setStyleSheet(xp_stylesheet())
    window = ExplorerWindow()
    window.show()
    app.exec()
