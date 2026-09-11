from __future__ import annotations

import os
import shutil
import sys
from collections import deque
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote, urlparse, urlunparse

import math
from PySide6.QtCore import (
    QDir,
    QEvent,
    QModelIndex,
    QObject,
    QPoint,
    QPointF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    QSettings,
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
    QPen,
    QPixmap,
    QPolygon,
    QPolygonF,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QDialog,
    QFileIconProvider,
    QFileSystemModel,
    QHBoxLayout,
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
    QToolButton,
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
from .dragdrop_views import ConfirmingDropTreeView, FavoritesListWidget, FileDragListWidget
from .network_panel import (
    NetworkPanel,
    mount_smb_share,
    normalize_network_share_input,
    resolve_smb_mount_paths,
    unmount_share,
)
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
    _FAVORITES_SETTINGS_KEY = "favorites/paths"

    def __init__(
        self,
        open_new_window_callback: Callable[[str | None], None] | None = None,
        initial_path: str | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("WinFile XP")
        self.resize(1200, 760)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._open_new_window_callback = open_new_window_callback
        self._settings = QSettings("WinFileXP", "WinFileXP")
        self._favorite_paths = self._load_favorite_paths()
        if not self._settings.contains(self._FAVORITES_SETTINGS_KEY):
            self._save_favorite_paths()

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
        self._pending_navigation_path: str | None = None
        self._pending_navigation_record_history = False
        self._pending_navigation_token = 0
        self._pending_navigation_timer = QTimer(self)
        self._pending_navigation_timer.setSingleShot(True)
        self._pending_navigation_timer.timeout.connect(self._apply_pending_navigation)

        self.new_window_action = QAction("New Window", self)
        self.new_window_action.setShortcut(QKeySequence.StandardKey.New)
        self.new_window_action.triggered.connect(self.open_new_window)

        self.close_window_action = QAction("Close Window", self)
        self.close_window_action.setShortcut(QKeySequence.StandardKey.Close)
        self.close_window_action.triggered.connect(self.close)

        self._setup_models()
        self._setup_views()
        self._setup_menus()
        self._setup_toolbar()
        self._setup_statusbar()
        self._setup_shortcuts()

        start_path = initial_path if initial_path and os.path.isdir(initial_path) else str(Path.home())
        self.navigate_to(start_path, record_history=True)

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

        favorites_section = QWidget(left_panel)
        favorites_layout = QVBoxLayout(favorites_section)
        favorites_layout.setContentsMargins(0, 0, 0, 0)
        favorites_layout.setSpacing(4)
        favorites_header = QLabel("Favorites", favorites_section)
        favorites_header.setStyleSheet("font-weight: bold; padding-left: 4px;")
        favorites_layout.addWidget(favorites_header)

        self.favorites_view = FavoritesListWidget(favorites_section)
        self.favorites_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.favorites_view.setDragEnabled(True)
        self.favorites_view.setAcceptDrops(True)
        self.favorites_view.setDropIndicatorShown(True)
        self.favorites_view.setDragDropMode(QListWidget.DragDrop)
        self.favorites_view.setDefaultDropAction(Qt.CopyAction)
        self.favorites_view.setAlternatingRowColors(True)
        self.favorites_view.setStyleSheet(
            "QListWidget {"
            " background: #fffdf3;"
            " border: 1px solid #b8ad8a;"
            " color: #000000;"
            " alternate-background-color: #fbf7e8;"
            " }"
            "QListWidget::item:selected:active {"
            " background: #316ac5;"
            " color: #ffffff;"
            " }"
            "QListWidget::item:selected:!active {"
            " background: #d7e2f2;"
            " color: #1f3358;"
            " }"
        )
        self.favorites_view.set_path_drop_callback(self._handle_favorites_drop)
        self.favorites_view.set_order_changed_callback(self._save_favorites_from_view_order)
        self.favorites_view.itemActivated.connect(self._on_favorite_activated)
        self.favorites_view.itemClicked.connect(self._on_favorite_activated)
        self.favorites_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.favorites_view.customContextMenuRequested.connect(self._show_favorites_context_menu)
        favorites_layout.addWidget(self.favorites_view)
        self._refresh_favorites_view()

        local_section = QWidget(left_panel)
        local_layout = QVBoxLayout(local_section)
        local_layout.setContentsMargins(0, 0, 0, 0)
        local_layout.setSpacing(4)
        local_header = QLabel("File System", local_section)
        local_header.setStyleSheet("font-weight: bold; padding-left: 4px;")
        local_layout.addWidget(local_header)

        self.tree_view = ConfirmingDropTreeView(local_section)
        self.tree_view.setModel(self.dir_model)
        self.tree_view.setHeaderHidden(True)
        self.tree_view.setColumnHidden(1, True)
        self.tree_view.setColumnHidden(2, True)
        self.tree_view.setColumnHidden(3, True)
        self.tree_view.setDragEnabled(True)
        self.tree_view.setAcceptDrops(True)
        self.tree_view.setDropIndicatorShown(True)
        self.tree_view.setDragDropMode(QTreeView.DragDrop)
        self.tree_view.setDefaultDropAction(Qt.MoveAction)
        self.tree_view.set_move_confirm_callback(self._confirm_drag_move)
        self.tree_view.clicked.connect(self._on_tree_clicked)
        self.tree_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_view.customContextMenuRequested.connect(self._show_file_system_context_menu)
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
            lambda path: self.request_navigate(path, record_history=True, defer_ms=120)
        )
        self.network_panel.edit_connection_requested.connect(
            self._edit_network_connection_parameters
        )
        self.network_panel.add_to_favorites_requested.connect(self._add_favorite_with_feedback)
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
        self.thumbnail_view.setDefaultDropAction(Qt.MoveAction)
        self.thumbnail_view.set_drop_target_path_callback(self.current_path)
        self.thumbnail_view.set_file_drop_callback(self._handle_thumbnail_drop)
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
        left_panel.setSizes([170, 360, 170])
        left_panel.setStretchFactor(0, 0)
        left_panel.setStretchFactor(1, 0)
        left_panel.setStretchFactor(2, 0)

        self.favorites_view.installEventFilter(self)
        self.tree_view.installEventFilter(self)
        self.network_panel.installEventFilter(self)

    def _setup_toolbar(self) -> None:
        toolbar = QToolBar("Navigation", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addWidget(
            self._create_toolbar_button(
                action=self.new_window_action,
                tooltip="New Window",
                icon=self._build_toolbar_action_icon("new_window"),
            )
        )
        toolbar.addSeparator()

        self.back_action = QAction("Back", self)
        self.back_action.setIcon(self._build_toolbar_action_icon("back"))
        self.back_action.triggered.connect(self.go_back)
        toolbar.addWidget(
            self._create_toolbar_button(
                action=self.back_action,
                tooltip="Back",
                icon=self.back_action.icon(),
            )
        )

        self.forward_action = QAction("Forward", self)
        self.forward_action.setIcon(self._build_toolbar_action_icon("forward"))
        self.forward_action.triggered.connect(self.go_forward)
        toolbar.addWidget(
            self._create_toolbar_button(
                action=self.forward_action,
                tooltip="Forward",
                icon=self.forward_action.icon(),
            )
        )

        self.up_action = QAction("Up", self)
        self.up_action.setIcon(self._build_toolbar_action_icon("up"))
        self.up_action.triggered.connect(self.go_up)
        toolbar.addWidget(
            self._create_toolbar_button(
                action=self.up_action,
                tooltip="Up",
                icon=self.up_action.icon(),
            )
        )

        toolbar.addSeparator()

        self.address_bar = QLineEdit(self)
        self.address_bar.setPlaceholderText("Path")
        self.address_bar.returnPressed.connect(self._on_address_enter)
        toolbar.addWidget(self.address_bar)

        self.go_action = QAction("Go", self)
        self.go_action.setIcon(self._build_toolbar_action_icon("go"))
        self.go_action.triggered.connect(self._on_address_enter)
        toolbar.addWidget(
            self._create_toolbar_button(
                action=self.go_action,
                tooltip="Go",
                icon=self.go_action.icon(),
            )
        )

        self.refresh_action = QAction("Refresh", self)
        self.refresh_action.setIcon(self._build_toolbar_action_icon("refresh"))
        self.refresh_action.triggered.connect(self.refresh)
        toolbar.addWidget(
            self._create_toolbar_button(
                action=self.refresh_action,
                tooltip="Refresh",
                icon=self.refresh_action.icon(),
            )
        )

        toolbar.addSeparator()

        self.view_mode_widget = QWidget(self)
        self.view_mode_layout = QHBoxLayout(self.view_mode_widget)
        self.view_mode_layout.setContentsMargins(0, 0, 0, 0)
        self.view_mode_layout.setSpacing(0)

        self.view_mode_group = QButtonGroup(self)
        self.view_mode_group.setExclusive(True)

        self.list_view_button = self._create_view_mode_button(
            mode="list",
            tooltip="Show in list",
            icon=self._build_view_mode_icon("list"),
        )
        self.thumbnail_view_button = self._create_view_mode_button(
            mode="thumbnail",
            tooltip="Show as icons",
            icon=self._build_view_mode_icon("thumbnail"),
        )

        self.view_mode_layout.addWidget(self.list_view_button)
        self.view_mode_layout.addWidget(self.thumbnail_view_button)
        toolbar.addWidget(self.view_mode_widget)
        self._update_view_mode_buttons()

    def _setup_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self.new_window_action)
        file_menu.addSeparator()
        file_menu.addAction(self.close_window_action)

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

        favorites_return_shortcut = QShortcut(QKeySequence(Qt.Key_Return), self.favorites_view)
        favorites_return_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        favorites_return_shortcut.activated.connect(self._on_favorites_enter)

        favorites_enter_shortcut = QShortcut(QKeySequence(Qt.Key_Enter), self.favorites_view)
        favorites_enter_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        favorites_enter_shortcut.activated.connect(self._on_favorites_enter)

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

    def _on_favorites_enter(self) -> None:
        item = self.favorites_view.currentItem()
        if item is not None:
            self._on_favorite_activated(item)

    def _on_tree_clicked(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        path = self.dir_model.filePath(index)
        if self._is_app_bundle(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            return
        self.request_navigate(path, record_history=True)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()

            if watched is self.favorites_view and key == Qt.Key_Down:
                if self.favorites_view.count() > 0 and self.favorites_view.currentRow() == self.favorites_view.count() - 1:
                    return self._focus_first_file_system_item()

            if watched is self.tree_view:
                current_index = self.tree_view.currentIndex()
                if current_index.isValid() and key == Qt.Key_Down:
                    if not self.tree_view.indexBelow(current_index).isValid():
                        return self._focus_first_network_item()
                if current_index.isValid() and key == Qt.Key_Up:
                    if not self.tree_view.indexAbove(current_index).isValid():
                        return self._focus_last_favorite_item()

            if watched is self.network_panel:
                current_item = self.network_panel.currentItem()
                if current_item is not None and key == Qt.Key_Up:
                    if self.network_panel.itemAbove(current_item) is None:
                        return self._focus_last_file_system_item()

        return super().eventFilter(watched, event)

    def _focus_first_file_system_item(self) -> bool:
        root_index = self.tree_view.rootIndex()
        first_index = self.dir_model.index(0, 0, root_index)
        if not first_index.isValid():
            return True
        self.tree_view.setFocus()
        self.tree_view.setCurrentIndex(first_index)
        self.tree_view.scrollTo(first_index)
        return True

    def _focus_last_file_system_item(self) -> bool:
        root_index = self.tree_view.rootIndex()
        first_index = self.dir_model.index(0, 0, root_index)
        if not first_index.isValid():
            return True

        last_index = first_index
        while True:
            next_index = self.tree_view.indexBelow(last_index)
            if not next_index.isValid():
                break
            last_index = next_index

        self.tree_view.setFocus()
        self.tree_view.setCurrentIndex(last_index)
        self.tree_view.scrollTo(last_index)
        return True

    def _focus_last_favorite_item(self) -> bool:
        count = self.favorites_view.count()
        if count <= 0:
            return self._focus_first_network_item()
        self.favorites_view.setFocus()
        self.favorites_view.setCurrentRow(count - 1)
        return True

    def _focus_first_network_item(self) -> bool:
        first_item = self.network_panel.topLevelItem(0)
        if first_item is None:
            return True
        self.network_panel.setFocus()
        self.network_panel.setCurrentItem(first_item)
        self.network_panel.scrollToItem(first_item)
        return True

    def _on_list_double_clicked(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        path = self.fs_model.filePath(index)
        if self._is_app_bundle(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            return
        if os.path.isdir(path):
            self.navigate_to(path, record_history=True)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _on_thumbnail_double_clicked(self, item: QListWidgetItem) -> None:
        self._defer_thumbnail_apply_after_interaction()
        path = item.data(Qt.UserRole)
        if not path:
            return
        if self._is_app_bundle(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
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

    def _handle_thumbnail_drop(
        self,
        destination_path: str,
        source_paths: list[str],
        is_move_drop: bool,
    ) -> bool:
        if not source_paths:
            return False

        target_dir = destination_path.strip() if destination_path else self.current_path()
        if not target_dir or not os.path.isdir(target_dir):
            return False

        if is_move_drop and not self._confirm_drag_move(target_dir, source_paths):
            return False

        failures: list[str] = []
        moved_count = 0
        copied_count = 0
        destination = Path(target_dir)

        for source_path_str in source_paths:
            source_path = Path(source_path_str)
            if not source_path.exists():
                failures.append(f"{source_path}: not found")
                continue

            if source_path.parent == destination:
                continue

            target_path = destination / source_path.name
            if target_path.exists():
                failures.append(f"{target_path}: destination already exists")
                continue

            try:
                if is_move_drop:
                    shutil.move(str(source_path), str(target_path))
                    moved_count += 1
                else:
                    if source_path.is_dir():
                        shutil.copytree(source_path, target_path)
                    else:
                        shutil.copy2(source_path, target_path)
                    copied_count += 1
            except OSError as error:
                failures.append(f"{source_path} -> {target_path}: {error}")

        self.refresh()

        if failures:
            QMessageBox.warning(
                self,
                "Drop",
                "Some items could not be transferred:\n" + "\n".join(failures),
            )

        if moved_count > 0:
            self.status.showMessage(f"Moved {moved_count} item(s)", 2500)
        elif copied_count > 0:
            self.status.showMessage(f"Copied {copied_count} item(s)", 2500)

        return moved_count > 0 or copied_count > 0 or not failures

    def _show_context_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction(self.new_window_action)
        menu.addSeparator()
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

    def _show_favorites_context_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction("Add Current Folder", lambda: self._add_favorite_with_feedback(self.current_path()))

        item = self.favorites_view.itemAt(pos)
        if item is not None:
            path = item.data(Qt.UserRole)
            if isinstance(path, str) and path:
                menu.addSeparator()
                menu.addAction("Open", lambda current_path=path: self.navigate_to(current_path, record_history=True))
                menu.addAction("Remove", lambda current_path=path: self._remove_favorite_path(current_path))

        menu.exec(self.favorites_view.viewport().mapToGlobal(pos))

    def _show_file_system_context_menu(self, pos: QPoint) -> None:
        index = self.tree_view.indexAt(pos)
        if not index.isValid():
            return

        path = self.dir_model.filePath(index)
        if not path:
            return

        menu = QMenu(self)
        menu.addAction("Browse", lambda target_path=path: self.navigate_to(target_path, record_history=True))
        menu.addAction("Add to Favorites", lambda target_path=path: self._add_favorite_with_feedback(target_path))
        menu.exec(self.tree_view.viewport().mapToGlobal(pos))

    def _on_favorite_activated(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if not isinstance(path, str) or not path:
            return
        if not os.path.isdir(path):
            QMessageBox.warning(
                self,
                "Favorites",
                f"Folder not found:\n{path}",
            )
            return
        self.navigate_to(path, record_history=True)

    def _handle_favorites_drop(self, dropped_paths: list[str]) -> bool:
        added = False
        for path in dropped_paths:
            if self._add_favorite_path(path, persist=False):
                added = True

        if added:
            self._save_favorite_paths()
            self._refresh_favorites_view()
            self.status.showMessage("Added to Favorites", 1800)
        return added

    def _save_favorites_from_view_order(self) -> None:
        ordered_paths: list[str] = []
        for row in range(self.favorites_view.count()):
            item = self.favorites_view.item(row)
            if item is None:
                continue
            path = item.data(Qt.UserRole)
            if not isinstance(path, str) or not path:
                continue
            ordered_paths.append(path)

        if ordered_paths:
            self._favorite_paths = ordered_paths
            self._save_favorite_paths()

    def _default_favorite_paths(self) -> list[str]:
        home = Path.home()
        candidates = [
            home,
            home / "Desktop",
            home / "Downloads",
        ]
        defaults: list[str] = []
        for candidate in candidates:
            path = str(candidate)
            if os.path.isdir(path):
                defaults.append(path)
        return defaults

    def _load_favorite_paths(self) -> list[str]:
        if not self._settings.contains(self._FAVORITES_SETTINGS_KEY):
            return self._default_favorite_paths()

        stored_paths = self._settings.value(self._FAVORITES_SETTINGS_KEY, [])
        if isinstance(stored_paths, str):
            stored_paths = [stored_paths]
        if not isinstance(stored_paths, list):
            return []

        cleaned: list[str] = []
        seen: set[str] = set()
        for raw_path in stored_paths:
            if not isinstance(raw_path, str):
                continue
            normalized = os.path.abspath(os.path.expanduser(raw_path.strip()))
            if not normalized or not os.path.isdir(normalized):
                continue
            key = os.path.normcase(normalized)
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(normalized)
        return cleaned

    def _save_favorite_paths(self) -> None:
        self._settings.setValue(self._FAVORITES_SETTINGS_KEY, self._favorite_paths)

    def _refresh_favorites_view(self) -> None:
        self.favorites_view.clear()
        icon = self.fs_model.iconProvider().icon(QFileIconProvider.Folder)
        for path in self._favorite_paths:
            item = QListWidgetItem(path)
            item.setData(Qt.UserRole, path)
            item.setToolTip(path)
            item.setIcon(icon)
            self.favorites_view.addItem(item)

    def _add_favorite_path(self, path: str, persist: bool = True) -> bool:
        normalized = os.path.abspath(os.path.expanduser(path.strip()))
        if not normalized or not os.path.isdir(normalized):
            return False

        key = os.path.normcase(normalized)
        for existing_path in self._favorite_paths:
            if os.path.normcase(existing_path) == key:
                return False

        self._favorite_paths.append(normalized)
        if persist:
            self._save_favorite_paths()
            self._refresh_favorites_view()
        return True

    def _add_favorite_with_feedback(self, path: str) -> None:
        if self._add_favorite_path(path):
            self.status.showMessage("Added to Favorites", 1800)
            return

        normalized = os.path.abspath(os.path.expanduser(path.strip()))
        if not normalized or not os.path.isdir(normalized):
            self.status.showMessage("Folder not available for Favorites", 2000)
            return

        self.status.showMessage("Already in Favorites", 1600)

    def _remove_favorite_path(self, path: str) -> None:
        key = os.path.normcase(path)
        self._favorite_paths = [
            existing_path
            for existing_path in self._favorite_paths
            if os.path.normcase(existing_path) != key
        ]
        self._save_favorite_paths()
        self._refresh_favorites_view()

    def current_path(self) -> str:
        root_index = self.list_view.rootIndex()
        if not root_index.isValid():
            return str(Path.home())
        return self.fs_model.filePath(root_index)

    @staticmethod
    def _is_windows_unc_path(path: str) -> bool:
        return os.name == "nt" and path.startswith("\\\\")

    def request_navigate(self, path: str, record_history: bool = False, defer_ms: int = 0) -> None:
        self._pending_navigation_token += 1
        self._pending_navigation_path = path
        self._pending_navigation_record_history = record_history

        if defer_ms > 0:
            self._pending_navigation_timer.start(defer_ms)
            return

        if self._pending_navigation_timer.isActive():
            self._pending_navigation_timer.stop()
        self._apply_pending_navigation()

    def _apply_pending_navigation(self) -> None:
        path = self._pending_navigation_path
        if path is None:
            return

        record_history = self._pending_navigation_record_history
        self._pending_navigation_path = None
        self._pending_navigation_record_history = False
        self.navigate_to(path, record_history=record_history)

    def navigate_to(self, path: str, record_history: bool = False) -> None:
        normalized = os.path.abspath(os.path.expanduser(path))
        if self._is_app_bundle(normalized):
            QDesktopServices.openUrl(QUrl.fromLocalFile(normalized))
            return

        self.fs_model.setRootPath(normalized)
        root_index = self.fs_model.index(normalized)
        if not root_index.isValid():
            QMessageBox.warning(self, "Invalid path", f"Folder not found:\n{normalized}")
            return

        if not self._is_windows_unc_path(normalized):
            self.dir_model.setRootPath(normalized)
            tree_index = self.dir_model.index(normalized)
            if tree_index.isValid():
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
        mount_root, _target_path = resolve_smb_mount_paths(entered)
        if mount_root is not None:
            self.connect_network_share(entered)
            return
        self.navigate_to(entered, record_history=True)

    def connect_network_share(self, share_url: str | None = None) -> None:
        target_url = normalize_network_share_input((share_url or "").strip())
        if not target_url:
            prompt = "SMB URL (example: smb://server/share):"
            default_text = "smb://"
            if os.name == "nt":
                prompt = "Network share path (example: \\\\server\\share):"
                default_text = "\\\\"
            target_url, ok = QInputDialog.getText(
                self,
                "Connect Network Share",
                prompt,
                text=default_text,
            )
            if not ok:
                return
            target_url = normalize_network_share_input(target_url.strip())

        if not target_url:
            return

        mount_root, target_path = resolve_smb_mount_paths(target_url)
        if mount_root is None:
            QMessageBox.warning(
                self,
                "Connect Network Share",
                "Invalid network share path. Use smb://server/share or \\\\server\\share.",
            )
            return

        if os.path.isdir(target_path):
            self.network_panel.register_known_windows_share(mount_root)
            self._refresh_network_panel_with_retries()
            self.navigate_to(target_path, record_history=True)
            return

        if os.path.isdir(mount_root):
            self.network_panel.register_known_windows_share(mount_root)
            self._refresh_network_panel_with_retries()
            self.navigate_to(mount_root, record_history=True)
            return

        if os.name == "nt":
            self._connect_windows_unc_share(mount_root, target_path)
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
        if os.name == "nt" and source_url.startswith("\\\\"):
            edited_path, ok = QInputDialog.getText(
                self,
                "Edit Connection Parameters",
                "Network share path (example: \\\\server\\share):",
                text=source_url,
            )
            if not ok:
                return
            edited_path = edited_path.strip()
            mount_root, target_path = resolve_smb_mount_paths(edited_path)
            if mount_root is None or target_path is None:
                QMessageBox.warning(
                    self,
                    "Edit Connection Parameters",
                    "Invalid UNC path. Use \\\\server\\share or \\\\server\\share\\folder.",
                )
                return

            if os.path.isdir(mount_path):
                unmount_share(mount_path)
            self.connect_network_share(edited_path)
            return

        parsed = urlparse(source_url)
        if parsed.scheme.lower() != "smb":
            QMessageBox.information(
                self,
                "Edit Connection Parameters",
                "Connection parameter editing is currently supported for SMB URLs and Windows UNC paths.",
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
            self.network_panel.register_known_windows_share(mount_root)
            self._refresh_network_panel_with_retries()
            self.navigate_to(target_path, record_history=True)
            return

        if os.path.isdir(mount_root):
            self.network_panel.register_known_windows_share(mount_root)
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

    @staticmethod
    def _split_unc_mount_root(path: str) -> tuple[str | None, str | None]:
        if not path.startswith("\\\\"):
            return None, None
        parts = [segment for segment in path.split("\\") if segment]
        if len(parts) < 2:
            return None, None
        return parts[0], parts[1]

    def _prompt_windows_credentials(self) -> tuple[str, str, bool] | None:
        username, ok = QInputDialog.getText(
            self,
            "Connect Network Share",
            "User name (DOMAIN\\user, server\\user, or user):",
            text="",
        )
        if not ok:
            return None
        username = username.strip()

        password, ok = QInputDialog.getText(
            self,
            "Connect Network Share",
            "Password (optional):",
            QLineEdit.Password,
            "",
        )
        if not ok:
            return None

        save_credentials = (
            QMessageBox.question(
                self,
                "Save Credentials",
                "Save credentials in Windows Credential Manager?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            == QMessageBox.Yes
        )
        return username, password, save_credentials

    def _connect_windows_unc_share(self, mount_root: str, target_path: str) -> None:
        creds = self._prompt_windows_credentials()
        if creds is None:
            return
        username, password, save_credentials = creds

        host, _share = self._split_unc_mount_root(mount_root)
        if save_credentials and username and host:
            cmdkey_args = [
                "cmdkey",
                f"/add:{host}",
                f"/user:{username}",
                f"/pass:{password}",
            ]
            try:
                cmdkey_result = subprocess.run(
                    cmdkey_args,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except OSError as error:
                QMessageBox.warning(
                    self,
                    "Connect Network Share",
                    f"Could not save credentials: {error}",
                )
                return
            if cmdkey_result.returncode != 0:
                detail = (cmdkey_result.stderr or cmdkey_result.stdout).strip() or "Unknown error"
                QMessageBox.warning(
                    self,
                    "Connect Network Share",
                    f"Could not save credentials in Credential Manager:\n{detail}",
                )
                return

        net_use_args = ["net", "use", mount_root]
        if username:
            net_use_args.append(password)
            net_use_args.append(f"/user:{username}")
        elif password:
            net_use_args.append(password)
        net_use_args.append("/persistent:no")

        try:
            net_use_result = subprocess.run(
                net_use_args,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except OSError as error:
            QMessageBox.warning(
                self,
                "Connect Network Share",
                f"Could not run net use: {error}",
            )
            return

        if net_use_result.returncode != 0:
            detail = (net_use_result.stderr or net_use_result.stdout).strip() or "Unknown error"
            QMessageBox.warning(
                self,
                "Connect Network Share",
                f"Windows could not connect to the share:\n{detail}",
            )
            return

        if os.path.isdir(target_path):
            self._refresh_network_panel_with_retries()
            self.navigate_to(target_path, record_history=True)
            return

        if os.path.isdir(mount_root):
            self._refresh_network_panel_with_retries()
            self.navigate_to(mount_root, record_history=True)
            return

        QMessageBox.warning(
            self,
            "Connect Network Share",
            "Share connected but path is still not accessible. Verify share path and permissions.",
        )

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

    def open_new_window(self) -> None:
        if self._open_new_window_callback is None:
            return
        self._open_new_window_callback(self.current_path())

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
        if self._is_app_bundle(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            return
        if os.path.isdir(path):
            self.navigate_to(path, record_history=True)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    @staticmethod
    def _is_app_bundle(path: str) -> bool:
        if sys.platform != "darwin":
            return False
        return os.path.isdir(path) and path.lower().endswith(".app")

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
        self.list_view.setRootIndex(index)
        if not self._is_windows_unc_path(current):
            tree_index = self.dir_model.index(current)
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

    def _create_toolbar_button(self, action: QAction, tooltip: str, icon: QIcon) -> QToolButton:
        button = QToolButton(self)
        button.setDefaultAction(action)
        button.setToolTip(tooltip)
        button.setIcon(icon)
        button.setIconSize(QSize(16, 16))
        button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.NoFocus)
        button.setStyleSheet(
            """
            QToolButton {
                border: none;
                background: transparent;
                padding: 4px 6px;
                margin: 0;
                border-radius: 4px;
            }
            QToolButton:hover {
                background: rgba(116, 99, 58, 0.08);
            }
            QToolButton:pressed {
                background: rgba(112, 137, 185, 0.18);
            }
            """
        )
        return button

    def _create_view_mode_button(self, mode: str, tooltip: str, icon: QIcon) -> QToolButton:
        button = QToolButton(self)
        button.setToolTip(tooltip)
        button.setCheckable(True)
        button.setAutoExclusive(True)
        button.setIcon(icon)
        button.setIconSize(QSize(16, 16))
        button.setFocusPolicy(Qt.NoFocus)
        button.setStyleSheet(
            """
            QToolButton {
                border: none;
                background: transparent;
                padding: 4px 6px;
                margin: 0;
                border-radius: 4px;
            }
            QToolButton:checked {
                background: rgba(112, 137, 185, 0.18);
            }
            QToolButton:hover {
                background: rgba(116, 99, 58, 0.08);
            }
            """
        )
        button.clicked.connect(lambda checked, current_mode=mode: self.set_view_mode(current_mode))
        self.view_mode_group.addButton(button)
        return button

    @staticmethod
    def _build_toolbar_action_icon(kind: str) -> QIcon:
        size = 20
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        icon_color = QColor("#898989")
        pen = QPen(icon_color, 1.8)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        if kind == "new_window":
            painter.setPen(QPen(QColor("#898989"), 1.8))
            painter.drawRoundedRect(2, 7, 10, 10, 2.5, 2.5)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#f4ff5a"))
            star_points = QPolygon([
                QPoint(13, -2),
                QPoint(15, 4),
                QPoint(21, 4),
                QPoint(16, 7),
                QPoint(18, 14),
                QPoint(13, 10),
                QPoint(8, 14),
                QPoint(10, 7),
                QPoint(5, 4),
                QPoint(11, 4),
            ])
            painter.drawPolygon(star_points)
        elif kind == "back":
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#898989"))
            painter.drawPolygon(QPolygon([
                QPoint(11, 2),
                QPoint(5, 8),
                QPoint(11, 14),
                QPoint(11, 10),
                QPoint(15, 10),
                QPoint(15, 6),
                QPoint(11, 6),
            ]))
        elif kind == "forward":
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#898989"))
            painter.drawPolygon(QPolygon([
                QPoint(9, 2),
                QPoint(15, 8),
                QPoint(9, 14),
                QPoint(9, 10),
                QPoint(5, 10),
                QPoint(5, 6),
                QPoint(9, 6),
            ]))
        elif kind == "up":
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#898989"))
            painter.drawPolygon(QPolygon([
                QPoint(2, 11),
                QPoint(8, 5),
                QPoint(14, 11),
                QPoint(10, 11),
                QPoint(10, 15),
                QPoint(6, 15),
                QPoint(6, 11),
            ]))
        elif kind == "go":
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#898989"))
            painter.drawPolygon(QPolygon([
                QPoint(2, 8),
                QPoint(11, 8),
                QPoint(11, 5),
                QPoint(17, 10),
                QPoint(11, 15),
                QPoint(11, 12),
                QPoint(2, 12),
            ]))
        elif kind == "refresh":
            # Draw the arc (270 degrees - 3/4 circle)
            arc_start_angle = 0
            arc_span = 270
            painter.drawArc(3, 3, 12, 12, arc_start_angle * 16, arc_span * 16)
            


        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _build_view_mode_icon(mode: str) -> QIcon:
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)

        if mode == "list":
            painter.setPen(QColor("#898989"))
            painter.setBrush(QColor("#898989"))
            for row_index in range(3):
                y = 3 + row_index * 4
                painter.drawEllipse(1, y, 2, 2)
                painter.drawRoundedRect(5, y, 9, 2, 1, 1)
        else:
            painter.setPen(QColor("#898989"))
            painter.setBrush(QColor("#898989"))
            cell_size = 5
            positions = [(1, 1), (9, 1), (1, 9), (9, 9)]
            for x, y in positions:
                painter.drawRect(x, y, cell_size, cell_size)

        painter.end()
        return QIcon(pixmap)

    def set_view_mode(self, mode: str) -> None:
        if mode not in {"list", "thumbnail"}:
            return

        if mode == self._view_mode:
            self._update_view_mode_buttons()
            return

        if mode == "thumbnail":
            self._view_mode = "thumbnail"
            self._populate_thumbnail_view(self.current_path())
            self.list_view.hide()
            self.thumbnail_view.show()
            self._ensure_thumbnail_width()
        else:
            self._view_mode = "list"
            self.thumbnail_view.hide()
            self.list_view.show()
            self._ensure_list_width()

        self._update_view_mode_buttons()
        self._update_status()

    def _update_view_mode_buttons(self) -> None:
        if hasattr(self, "list_view_button") and hasattr(self, "thumbnail_view_button"):
            self.list_view_button.setChecked(self._view_mode == "list")
            self.thumbnail_view_button.setChecked(self._view_mode == "thumbnail")

    def toggle_view_mode(self) -> None:
        target_mode = "thumbnail" if self._view_mode == "list" else "list"
        self.set_view_mode(target_mode)

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

    class _WindowManager(QObject):
        def __init__(self) -> None:
            super().__init__()
            self._windows: list[ExplorerWindow] = []

        def create_window(self, start_path: str | None = None) -> ExplorerWindow:
            window = ExplorerWindow(
                open_new_window_callback=self.create_window,
                initial_path=start_path,
            )
            self._windows.append(window)
            window.destroyed.connect(lambda *_: self._on_window_destroyed(window))

            window.show()
            window.raise_()
            window.activateWindow()
            return window

        def _on_window_destroyed(self, window: ExplorerWindow) -> None:
            if window in self._windows:
                self._windows.remove(window)

    window_manager = _WindowManager()
    window_manager.create_window()
    app.exec()
