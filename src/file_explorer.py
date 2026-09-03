from __future__ import annotations

import os
from collections import deque
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

from PySide6.QtCore import (
    QDir,
    QModelIndex,
    QObject,
    QPoint,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
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
    QWidget,
)

from .dialogs import (
    ActionConfirmDialog,
    DeleteConfirmDialog,
    build_delete_confirmation_message,
    build_move_confirmation_message,
)
from .dragdrop_views import ConfirmingDropTreeView, FileDragListWidget
from .network_panel import NetworkPanel, mount_smb_share, resolve_smb_mount_paths, unmount_share
from .file_operations import create_folder, delete_items, paste_items, rename_item
from .navigation_state import NavigationHistory
from .thumbnail_previews import ThumbnailPreviewProvider
from .ui_theme import XPIconProvider, xp_stylesheet


class _ThumbnailWorkerSignals(QObject):
    previews_ready = Signal(int, object)
    finished = Signal(int)


class _ThumbnailPreviewWorker(QRunnable):
    def __init__(
        self,
        token: int,
        provider: ThumbnailPreviewProvider,
        icon_size: QSize,
        paths: list[str],
        allow_expensive_previews: bool,
    ) -> None:
        super().__init__()
        self.token = token
        self.provider = provider
        self.icon_size = icon_size
        self.paths = paths
        self.allow_expensive_previews = allow_expensive_previews
        self.signals = _ThumbnailWorkerSignals()

    def run(self) -> None:
        batch: list[tuple[str, QImage | None]] = []
        for path in self.paths:
            preview = self.provider.preview_image_for_path(
                path,
                self.icon_size,
                allow_expensive_previews=self.allow_expensive_previews,
            )
            if preview is not None and not preview.isNull():
                batch.append((path, preview))
            else:
                batch.append((path, None))

            if len(batch) >= 6:
                self.signals.previews_ready.emit(self.token, batch)
                batch = []
        if batch:
            self.signals.previews_ready.emit(self.token, batch)
        self.signals.finished.emit(self.token)


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
        self._network_poll_attempts = 0
        self._thumbnail_render_token = 0
        self._thumbnail_pending_items: list[tuple[QListWidgetItem, str]] = []
        self._thumbnail_pending_index = 0
        self._thumbnail_batch_size = 6
        self._thumbnail_fast_mode = False
        self._thumbnail_path_to_item: dict[str, QListWidgetItem] = {}
        self._thumbnail_thread_pool = QThreadPool(self)
        self._thumbnail_thread_pool.setMaxThreadCount(2)
        self._thumbnail_worker_chunk_size = 12
        self._thumbnail_workers: set[_ThumbnailPreviewWorker] = set()
        self._thumbnail_ghost_icon: QIcon | None = None
        self._thumbnail_ready_previews: deque[tuple[int, str, QImage]] = deque()
        self._thumbnail_apply_batch_size = 4
        self._thumbnail_apply_timer = QTimer(self)
        self._thumbnail_apply_timer.setSingleShot(True)
        self._thumbnail_apply_timer.timeout.connect(self._apply_ready_thumbnail_previews)
        self._thumbnail_ghost_paths: set[str] = set()
        self._thumbnail_preview_attempts: dict[str, int] = {}
        self._thumbnail_inflight_paths: set[str] = set()
        self._thumbnail_retry_batch_size = 3
        self._thumbnail_retry_max_attempts = 6
        self._thumbnail_retry_timer = QTimer(self)
        self._thumbnail_retry_timer.setSingleShot(True)
        self._thumbnail_retry_timer.timeout.connect(self._retry_ghost_thumbnails)

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
        self.fs_model.setRootPath(str(Path.home()))
        self.fs_model.directoryLoaded.connect(self._on_directory_loaded)
        self.dir_model = QFileSystemModel(self)
        self.dir_model.setReadOnly(False)
        self.dir_model.setFilter(QDir.AllDirs | QDir.NoDotAndDotDot)
        self.dir_model.setIconProvider(icon_provider)
        self.dir_model.setRootPath(str(Path.home()))

    def _setup_views(self) -> None:
        self.splitter = QSplitter(self)
        self.setCentralWidget(self.splitter)

        left_panel = QSplitter(Qt.Vertical, self.splitter)

        local_section = QWidget(left_panel)
        local_layout = QVBoxLayout(local_section)
        local_layout.setContentsMargins(0, 0, 0, 0)
        local_layout.setSpacing(4)
        local_header = QLabel("Local folders", local_section)
        local_header.setStyleSheet("font-weight: bold; padding-left: 4px;")
        local_layout.addWidget(local_header)

        self.tree_view = ConfirmingDropTreeView(local_section)
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
        # Disable edit triggers (no rename on Enter or double-click)
        self.tree_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        local_layout.addWidget(self.tree_view)

        network_section = QWidget(left_panel)
        network_layout = QVBoxLayout(network_section)
        network_layout.setContentsMargins(0, 0, 0, 0)
        network_layout.setSpacing(4)
        network_header = QLabel("Network", network_section)
        network_header.setStyleSheet("font-weight: bold; padding-left: 4px;")
        network_layout.addWidget(network_header)

        self.network_panel = NetworkPanel(
            connect_callback=self.connect_network_share,
            parent=network_section,
        )
        self.network_panel.navigate_requested.connect(
            lambda path: self.navigate_to(path, record_history=True)
        )
        self.network_panel.edit_connection_requested.connect(
            self._edit_network_connection_parameters
        )
        network_layout.addWidget(self.network_panel)

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

        self.thumbnail_view = FileDragListWidget(self.splitter)
        self.thumbnail_view.setViewMode(QListWidget.IconMode)
        self.thumbnail_view.setResizeMode(QListWidget.Adjust)
        self.thumbnail_view.setIconSize(QSize(96, 96))
        self.thumbnail_view.setGridSize(QSize(132, 152))
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
        left_panel.setSizes([420, 140])
        left_panel.setStretchFactor(0, 0)
        left_panel.setStretchFactor(1, 0)

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

        tree_return_shortcut = QShortcut(QKeySequence(Qt.Key_Return), self.tree_view)
        tree_return_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        tree_return_shortcut.activated.connect(self._on_tree_enter)

        tree_enter_shortcut = QShortcut(QKeySequence(Qt.Key_Enter), self.tree_view)
        tree_enter_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        tree_enter_shortcut.activated.connect(self._on_tree_enter)

        QShortcut(QKeySequence.StandardKey.Copy, self, activated=self.copy_selected)
        QShortcut(QKeySequence.StandardKey.Cut, self, activated=self.cut_selected)
        QShortcut(QKeySequence.StandardKey.Paste, self, activated=self.paste_into_current)

        QShortcut(QKeySequence("Ctrl+T"), self, activated=self.open_terminal)

        QShortcut(QKeySequence("Alt+D"), self, activated=self.focus_address_bar)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.focus_address_bar)

    def _on_tree_enter(self) -> None:
        index = self.tree_view.currentIndex()
        if index.isValid():
            self._on_tree_clicked(index)

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
        self._defer_thumbnail_apply_after_interaction()
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
        menu.addAction("Edit", self.edit_selected)
        menu.addAction("Rename", self.rename_selected)
        menu.addAction("Delete", self.delete_selected)
        menu.addSeparator()
        menu.addAction("New Folder", self.new_folder)
        menu.addAction("Open Terminal", self.open_terminal)
        menu.addAction("Refresh", self.refresh)
        source = self.sender()
        if isinstance(source, (QTreeView, QListWidget)):
            global_pos = source.viewport().mapToGlobal(pos)
        else:
            global_pos = self.mapToGlobal(pos)
        menu.exec(global_pos)

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

        self.fs_model.setRootPath(normalized)
        self.dir_model.setRootPath(normalized)
        root_index = self.fs_model.index(normalized)
        tree_index = self.dir_model.index(normalized)
        self.tree_view.setCurrentIndex(tree_index)
        self.tree_view.scrollTo(tree_index)
        self.list_view.setRootIndex(root_index)
        self.address_bar.setText(normalized)

        if self._view_mode == "thumbnail":
            self._populate_thumbnail_view(normalized)

        if record_history:
            self.navigation_history.record(normalized)
        self._update_nav_actions()
        self._update_status()

    def _on_directory_loaded(self, path: str) -> None:
        """Called by QFileSystemModel once it finishes scanning a directory.

        Refreshes the list view root index so the view fills immediately after
        the model completes async directory scanning instead of appearing blank.
        """
        current = self.current_path()
        if os.path.normpath(path) == os.path.normpath(current):
            self.list_view.setRootIndex(self.fs_model.index(current))
            self._update_status()

    def _on_address_enter(self) -> None:
        entered = self.address_bar.text().strip()
        if entered.lower().startswith("smb://"):
            self.connect_network_share(entered)
            return
        self.navigate_to(entered, record_history=True)

    def connect_network_share(self, share_url: str | None = None) -> None:
        target_url = (share_url or "").strip()
        if not target_url:
            target_url, ok = QInputDialog.getText(
                self,
                "Connect Network Share",
                "SMB URL (example: smb://server/share):",
                text="smb://",
            )
            if not ok:
                return
            target_url = target_url.strip()

        if not target_url:
            return

        mount_root, target_path = resolve_smb_mount_paths(target_url)
        if mount_root is None:
            QMessageBox.warning(self, "Connect Network Share", "Invalid SMB URL.")
            return

        if os.path.isdir(target_path):
            self._refresh_network_panel_with_retries()
            self.navigate_to(target_path, record_history=True)
            return

        if os.path.isdir(mount_root):
            self._refresh_network_panel_with_retries()
            self.navigate_to(mount_root, record_history=True)
            return

        if not mount_smb_share(target_url):
            QMessageBox.warning(
                self,
                "Connect Network Share",
                "Could not initiate SMB connection. Check address and try again.",
            )
            return

        self.status.showMessage(
            "Connecting to network share. Complete login if prompted...",
            6000,
        )
        self._network_poll_attempts = 0
        self._poll_for_mounted_share(mount_root, target_path)

    def _edit_network_connection_parameters(self, mount_path: str, source_url: str) -> None:
        parsed = urlparse(source_url)
        if parsed.scheme.lower() != "smb":
            QMessageBox.information(
                self,
                "Edit Connection Parameters",
                "Connection parameter editing is currently supported for SMB shares only.",
            )
            return

        current_username = parsed.username or ""
        username, ok = QInputDialog.getText(
            self,
            "Edit Connection Parameters",
            "User name (leave empty to prompt at connect):",
            text=current_username,
        )
        if not ok:
            return
        username = username.strip()

        password, ok = QInputDialog.getText(
            self,
            "Edit Connection Parameters",
            "Password (optional):",
            QLineEdit.Password,
            "",
        )
        if not ok:
            return

        host = parsed.hostname or ""
        if not host or not parsed.path:
            QMessageBox.warning(
                self,
                "Edit Connection Parameters",
                "Cannot parse current SMB connection URL.",
            )
            return

        if username:
            userinfo = quote(username, safe="")
            if password:
                userinfo = f"{userinfo}:{quote(password, safe='')}"
            netloc = f"{userinfo}@{host}"
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
        else:
            netloc = host
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"

        rebuilt_url = urlunparse(("smb", netloc, parsed.path, "", "", ""))

        if os.path.isdir(mount_path):
            unmount_share(mount_path)
        self.connect_network_share(rebuilt_url)

    def _poll_for_mounted_share(self, mount_root: str, target_path: str) -> None:
        if os.path.isdir(target_path):
            self._refresh_network_panel_with_retries()
            self.navigate_to(target_path, record_history=True)
            return

        if os.path.isdir(mount_root):
            self._refresh_network_panel_with_retries()
            self.navigate_to(mount_root, record_history=True)
            return

        self._network_poll_attempts += 1
        if self._network_poll_attempts > 20:
            QMessageBox.information(
                self,
                "Connect Network Share",
                "Share was not mounted yet. If login prompt is open, finish it and retry.\n\n"
                f"Expected mount path:\n{mount_root}",
            )
            return

        QTimer.singleShot(1000, lambda: self._poll_for_mounted_share(mount_root, target_path))

    def _refresh_network_panel_with_retries(self, retries: int = 4, delay_ms: int = 500) -> None:
        """Refresh network panel multiple times to absorb post-mount timing lag."""
        self.network_panel.refresh_shares()
        if retries <= 0:
            return
        QTimer.singleShot(
            delay_ms,
            lambda: self._refresh_network_panel_with_retries(retries - 1, delay_ms),
        )

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
        if self._view_mode == "thumbnail":
            self._defer_thumbnail_apply_after_interaction()
        path = paths[0]
        if os.path.isdir(path):
            self.navigate_to(path, record_history=True)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def edit_selected(self) -> None:
        paths = self.selected_paths()
        if not paths:
            return
        path = paths[0]
        if os.path.isdir(path):
            return

        import platform
        import shutil
        try:
            if platform.system() == "Darwin":  # macOS
                os.system(f'open -a TextEdit "{path}"')
            elif platform.system() == "Linux":
                # Try common text editors in order of preference
                editors = ["geany","code", "gedit", "kate", "mousepad", "leafpad", "nano"]
                for editor in editors:
                    if shutil.which(editor):
                        os.system(f'{editor} "{path}" &')
                        return
                # Fallback to xdg-open if no specific editor found
                os.system(f'xdg-open "{path}"')
            elif platform.system() == "Windows":
                os.system(f'notepad "{path}"')
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except OSError as error:
            QMessageBox.critical(self, "Edit failed", str(error))

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
        index = self.fs_model.index(current)
        tree_index = self.dir_model.index(current)
        self.list_view.setRootIndex(index)
        self.tree_view.setCurrentIndex(tree_index)
        self.tree_view.scrollTo(tree_index)
        if self._view_mode == "thumbnail":
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
        self._thumbnail_render_token += 1
        token = self._thumbnail_render_token
        self._thumbnail_pending_items = []
        self._thumbnail_pending_index = 0
        self._thumbnail_path_to_item = {}
        self._thumbnail_workers = set()
        self._thumbnail_ready_previews = deque()
        self._thumbnail_apply_timer.stop()
        self._thumbnail_retry_timer.stop()
        self._thumbnail_ghost_paths = set()
        self._thumbnail_preview_attempts = {}
        self._thumbnail_inflight_paths = set()

        self.thumbnail_view.clear()
        self.thumbnail_view.setUpdatesEnabled(False)
        try:
            icon_provider = self.fs_model.iconProvider()
            ghost_icon = self._build_ghost_thumbnail_icon(self.thumbnail_view.iconSize())

            entries = [entry for entry in os.scandir(path) if not entry.name.startswith('.')]
            self._thumbnail_fast_mode = len(entries) > 120
            preview_paths: list[str] = []

            for entry in entries:
                entry_path = entry.path

                item = QListWidgetItem()
                item.setText(entry.name)
                item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
                item.setIcon(ghost_icon)

                item.setData(Qt.UserRole, entry_path)
                self.thumbnail_view.addItem(item)
                self._thumbnail_path_to_item[entry_path] = item
                self._thumbnail_ghost_paths.add(entry_path)
                self._thumbnail_preview_attempts[entry_path] = 0

                if (
                    self.thumbnail_provider is not None
                    and self.thumbnail_provider.supports_background_preview(
                        entry_path,
                        allow_expensive_previews=not self._thumbnail_fast_mode,
                    )
                ):
                    preview_paths.append(entry_path)
                else:
                    self._thumbnail_pending_items.append((item, entry_path))

            preview_paths = self._order_thumbnail_paths_by_visibility(preview_paths)
            if self._thumbnail_pending_items:
                ordered_pending_paths = self._order_thumbnail_paths_by_visibility(
                    [entry_path for _, entry_path in self._thumbnail_pending_items]
                )
                pending_by_path = {
                    entry_path: (item, entry_path)
                    for item, entry_path in self._thumbnail_pending_items
                }
                self._thumbnail_pending_items = [
                    pending_by_path[entry_path]
                    for entry_path in ordered_pending_paths
                    if entry_path in pending_by_path
                ]

            # In very large folders, skip expensive non-image preview generation
            # on initial rendering to keep switching responsive.
            if self._thumbnail_pending_items:
                QTimer.singleShot(0, lambda: self._process_thumbnail_batch(token))

            if self.thumbnail_provider is not None and preview_paths:
                self._start_thumbnail_preview_workers(token, preview_paths)

            if self._thumbnail_ghost_paths and not self._thumbnail_retry_timer.isActive():
                self._thumbnail_retry_timer.start(900)
        except OSError:
            pass
        finally:
            self.thumbnail_view.setUpdatesEnabled(True)

    def _start_thumbnail_preview_workers(self, token: int, paths: list[str]) -> None:
        if self.thumbnail_provider is None:
            return

        icon_size = self.thumbnail_view.iconSize()
        allow_expensive = not self._thumbnail_fast_mode
        for offset in range(0, len(paths), self._thumbnail_worker_chunk_size):
            chunk = paths[offset : offset + self._thumbnail_worker_chunk_size]
            for path in chunk:
                self._thumbnail_inflight_paths.add(path)
                self._thumbnail_preview_attempts[path] = self._thumbnail_preview_attempts.get(path, 0) + 1
            worker = _ThumbnailPreviewWorker(
                token=token,
                provider=self.thumbnail_provider,
                icon_size=icon_size,
                paths=chunk,
                allow_expensive_previews=allow_expensive,
            )
            worker.signals.previews_ready.connect(self._on_thumbnail_previews_ready)
            worker.signals.finished.connect(
                lambda finished_token, current_worker=worker: self._on_thumbnail_worker_finished(
                    finished_token,
                    current_worker,
                )
            )
            self._thumbnail_workers.add(worker)
            self._thumbnail_thread_pool.start(worker, -1)

    def _on_thumbnail_previews_ready(self, token: int, previews_obj: object) -> None:
        if token != self._thumbnail_render_token:
            return
        if self._view_mode != "thumbnail":
            return
        if not isinstance(previews_obj, list):
            return

        for preview_pair in previews_obj:
            if not isinstance(preview_pair, tuple) or len(preview_pair) != 2:
                continue
            path, preview_obj = preview_pair
            if not isinstance(path, str):
                continue
            self._thumbnail_inflight_paths.discard(path)

            if preview_obj is None:
                continue
            if not isinstance(preview_obj, QImage) or preview_obj.isNull():
                continue
            self._thumbnail_ready_previews.append((token, path, preview_obj))

        if not self._thumbnail_apply_timer.isActive():
            self._thumbnail_apply_timer.start(8)

    def _apply_ready_thumbnail_previews(self) -> None:
        if self._view_mode != "thumbnail":
            self._thumbnail_ready_previews = deque()
            return
        if self.thumbnail_provider is None:
            self._thumbnail_ready_previews = deque()
            return

        applied = 0
        self.thumbnail_view.setUpdatesEnabled(False)
        try:
            while self._thumbnail_ready_previews and applied < self._thumbnail_apply_batch_size:
                token, path, preview = self._thumbnail_ready_previews.popleft()
                if token != self._thumbnail_render_token:
                    continue

                item = self._thumbnail_path_to_item.get(path)
                if item is None or item.listWidget() is not self.thumbnail_view:
                    continue

                item.setIcon(
                    self.thumbnail_provider.icon_from_preview_image(
                        preview,
                        self.thumbnail_view.iconSize(),
                    )
                )
                self._thumbnail_ghost_paths.discard(path)
                self._thumbnail_preview_attempts.pop(path, None)
                applied += 1
        finally:
            self.thumbnail_view.setUpdatesEnabled(True)

        if self._thumbnail_ready_previews:
            # Keep yielding to user input (double-click/open/scroll) while
            # thumbnails continue rendering at lower priority.
            self._thumbnail_apply_timer.start(12)
            return

        if self._thumbnail_ghost_paths and not self._thumbnail_retry_timer.isActive():
            self._thumbnail_retry_timer.start(900)

    def _on_thumbnail_worker_finished(
        self,
        token: int,
        worker: _ThumbnailPreviewWorker,
    ) -> None:
        self._thumbnail_workers.discard(worker)
        if token != self._thumbnail_render_token:
            return
        if self._thumbnail_ghost_paths and not self._thumbnail_retry_timer.isActive():
            self._thumbnail_retry_timer.start(1200)

    def _process_thumbnail_batch(self, token: int) -> None:
        if token != self._thumbnail_render_token:
            return
        if self._view_mode != "thumbnail":
            return
        if self.thumbnail_provider is None:
            return

        start = self._thumbnail_pending_index
        end = min(start + self._thumbnail_batch_size, len(self._thumbnail_pending_items))
        icon_size = self.thumbnail_view.iconSize()

        self.thumbnail_view.setUpdatesEnabled(False)
        try:
            for index in range(start, end):
                item, entry_path = self._thumbnail_pending_items[index]
                if item.listWidget() is not self.thumbnail_view:
                    continue
                if self.thumbnail_provider is not None:
                    item.setIcon(
                        self.thumbnail_provider.icon_for_path(
                            entry_path,
                            icon_size,
                            allow_expensive_previews=not self._thumbnail_fast_mode,
                        )
                    )
                elif os.path.isdir(entry_path):
                    item.setIcon(self.fs_model.iconProvider().icon(QFileIconProvider.Folder))
                else:
                    item.setIcon(self.fs_model.iconProvider().icon(QFileIconProvider.File))
                self._thumbnail_ghost_paths.discard(entry_path)
                self._thumbnail_preview_attempts.pop(entry_path, None)
                self._thumbnail_inflight_paths.discard(entry_path)
        finally:
            self.thumbnail_view.setUpdatesEnabled(True)

        self._thumbnail_pending_index = end
        if self._thumbnail_pending_index < len(self._thumbnail_pending_items):
            QTimer.singleShot(10, lambda: self._process_thumbnail_batch(token))
            return

        self._thumbnail_pending_items = []
        self._thumbnail_pending_index = 0

    def _order_thumbnail_paths_by_visibility(self, paths: list[str]) -> list[str]:
        if not paths:
            return paths

        viewport = self.thumbnail_view.viewport()
        viewport_rect = viewport.rect()
        top_left_item = self.thumbnail_view.itemAt(4, 4)
        top_left_row = self.thumbnail_view.row(top_left_item) if top_left_item is not None else 0

        prioritized: list[tuple[int, int, str]] = []
        for path in paths:
            item = self._thumbnail_path_to_item.get(path)
            if item is None:
                continue

            rect = self.thumbnail_view.visualItemRect(item)
            if rect.isValid() and rect.intersects(viewport_rect):
                zone_rank = 0
            elif rect.isValid() and rect.top() < viewport_rect.top():
                zone_rank = 1
            else:
                zone_rank = 2

            row = self.thumbnail_view.row(item)
            distance = abs(row - top_left_row)
            prioritized.append((zone_rank, distance, path))

        if not prioritized:
            return paths

        prioritized.sort(key=lambda item: (item[0], item[1]))
        ordered = [path for _, _, path in prioritized]

        # Keep any unmapped paths at the end (defensive ordering fallback).
        mapped = set(ordered)
        for path in paths:
            if path not in mapped:
                ordered.append(path)
        return ordered

    def _retry_ghost_thumbnails(self) -> None:
        if self._view_mode != "thumbnail":
            return
        if self.thumbnail_provider is None:
            return
        if not self._thumbnail_ghost_paths:
            return

        allow_expensive = not self._thumbnail_fast_mode
        candidates: list[str] = []
        for path in list(self._thumbnail_ghost_paths):
            if path in self._thumbnail_inflight_paths:
                continue
            if path not in self._thumbnail_path_to_item:
                continue
            attempts = self._thumbnail_preview_attempts.get(path, 0)
            if attempts >= self._thumbnail_retry_max_attempts:
                item = self._thumbnail_path_to_item.get(path)
                if item is not None:
                    item.setIcon(self.fs_model.iconProvider().icon(QFileIconProvider.File))
                self._thumbnail_ghost_paths.discard(path)
                self._thumbnail_preview_attempts.pop(path, None)
                continue
            if not self.thumbnail_provider.supports_background_preview(
                path,
                allow_expensive_previews=allow_expensive,
            ):
                continue
            candidates.append(path)
            if len(candidates) >= self._thumbnail_retry_batch_size:
                break

        if candidates:
            self._start_thumbnail_preview_workers(self._thumbnail_render_token, candidates)

        if self._thumbnail_ghost_paths:
            self._thumbnail_retry_timer.start(1400)

    def _build_ghost_thumbnail_icon(self, icon_size: QSize) -> QIcon:
        if self._thumbnail_ghost_icon is not None:
            cached_size = self._thumbnail_ghost_icon.actualSize(icon_size)
            if cached_size == icon_size:
                return self._thumbnail_ghost_icon

        canvas = QPixmap(icon_size)
        canvas.fill(Qt.transparent)

        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(180, 186, 197, 110))
        margin = max(6, int(min(icon_size.width(), icon_size.height()) * 0.08))
        painter.drawRoundedRect(
            margin,
            margin,
            icon_size.width() - (margin * 2),
            icon_size.height() - (margin * 2),
            10,
            10,
        )
        painter.end()

        self._thumbnail_ghost_icon = QIcon(canvas)
        return self._thumbnail_ghost_icon

    def _defer_thumbnail_apply_after_interaction(self, delay_ms: int = 260) -> None:
        if self._view_mode != "thumbnail":
            return
        self._thumbnail_apply_timer.stop()
        token = self._thumbnail_render_token

        def resume_apply() -> None:
            if token != self._thumbnail_render_token:
                return
            if self._view_mode != "thumbnail":
                return
            if self._thumbnail_ready_previews and not self._thumbnail_apply_timer.isActive():
                self._thumbnail_apply_timer.start(0)

        QTimer.singleShot(delay_ms, resume_apply)

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
