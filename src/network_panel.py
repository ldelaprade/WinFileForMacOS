from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from urllib.parse import unquote, urlparse

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QTreeWidget, QTreeWidgetItem, QWidget, QFileIconProvider

from .ui_theme import XPIconProvider


def get_mounted_network_shares() -> list[tuple[str, str, str]]:
    """Return (display_name, mount_path, source_url) for each mounted network share."""
    try:
        result = subprocess.run(
            ["mount"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except OSError:
        return []

    shares: list[tuple[str, str, str]] = []
    for line in result.stdout.splitlines():
        if " on " not in line:
            continue
        lower = line.lower()
        if not any(t in lower for t in ("smbfs", "nfs", "afpfs", "cifs", "webdav")):
            continue
        source, rest = line.split(" on ", 1)
        mount_path = rest.strip().split(" ")[0]
        display = mount_path.rsplit("/", 1)[-1] or mount_path
        source_url = _mounted_source_to_url(source.strip(), lower)
        shares.append((display, mount_path, source_url))
    return shares


def _mounted_source_to_url(source: str, lower_mount_line: str) -> str:
    if "smbfs" in lower_mount_line or "cifs" in lower_mount_line:
        # macOS often reports SMB source as //user@host/share.
        smb_source = source[2:] if source.startswith("//") else source
        if "@" in smb_source:
            smb_source = smb_source.split("@", 1)[1]
        return f"smb://{smb_source}"

    if "nfs" in lower_mount_line:
        # Typical source format: host:/export/path
        if ":" in source:
            host, export_path = source.split(":", 1)
            if export_path.startswith("/"):
                return f"nfs://{host}{export_path}"
        return f"nfs://{source}"

    if "afpfs" in lower_mount_line:
        afp_source = source[2:] if source.startswith("//") else source
        if "@" in afp_source:
            afp_source = afp_source.split("@", 1)[1]
        return f"afp://{afp_source}"

    if "webdav" in lower_mount_line:
        return source

    return source


def mount_smb_share(smb_url: str) -> bool:
    """Trigger macOS to mount an SMB share via AppleScript — no Finder window."""
    try:
        subprocess.Popen(
            ["osascript", "-e", f'mount volume "{smb_url}"'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except OSError:
        return False


def unmount_share(mount_path: str) -> bool:
    """Unmount a network share by its local mount path."""
    try:
        subprocess.run(
            ["diskutil", "unmount", mount_path],
            capture_output=True,
            timeout=10,
            check=True,
        )
        return True
    except Exception:
        return False


def resolve_smb_mount_paths(smb_url: str) -> tuple[str | None, str | None]:
    """Parse smb://host/share[/sub/path] → (mount_root, target_path).

    Returns (None, None) when the URL is not a valid SMB URL.
    """
    parsed = urlparse(smb_url)
    if parsed.scheme.lower() != "smb" or not parsed.netloc:
        return None, None
    path_parts = [p for p in parsed.path.split("/") if p]
    if not path_parts:
        return None, None
    share_name = unquote(path_parts[0])
    mount_root = f"/Volumes/{share_name}"
    if len(path_parts) == 1:
        return mount_root, mount_root
    sub_parts = [unquote(p) for p in path_parts[1:]]
    target_path = "/".join([mount_root] + sub_parts)
    return mount_root, target_path


class NetworkPanel(QTreeWidget):
    """Sidebar widget listing mounted network shares.

    Mounted shares appear as direct list entries. Right-click empty space to
    connect a new share; right-click a share to browse or disconnect.
    """

    navigate_requested: Signal = Signal(str)
    edit_connection_requested: Signal = Signal(str, str)
    _PATH_ROLE = Qt.UserRole
    _IS_SHARE_ROLE = Qt.UserRole + 1
    _LOADED_ROLE = Qt.UserRole + 2
    _SOURCE_URL_ROLE = Qt.UserRole + 3

    def __init__(
        self,
        connect_callback: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._connect_callback = connect_callback
        style = QApplication.style()
        self._network_root_icon = style.standardIcon(QStyle.SP_DriveNetIcon)
        icon_provider = XPIconProvider()
        self._folder_icon = icon_provider.icon(QFileIconProvider.Folder)

        self.setHeaderHidden(True)
        self.setRootIsDecorated(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.itemClicked.connect(self._on_item_clicked)
        self.itemExpanded.connect(self._on_item_expanded)

        self.refresh_shares()

    def refresh_shares(self) -> None:
        """Re-scan mounted network shares and repopulate the tree."""
        self.clear()
        for display_name, mount_path, source_url in get_mounted_network_shares():
            item = QTreeWidgetItem(self, [f"{display_name} ({source_url})"])
            item.setData(0, self._PATH_ROLE, mount_path)
            item.setData(0, self._IS_SHARE_ROLE, True)
            item.setData(0, self._LOADED_ROLE, False)
            item.setData(0, self._SOURCE_URL_ROLE, source_url)
            item.setIcon(0, self._network_root_icon)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if self._has_subdirectories(mount_path):
                self._add_placeholder_child(item)

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        path = item.data(0, self._PATH_ROLE)
        if path:
            self.navigate_requested.emit(path)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            item = self.currentItem()
            if item is not None:
                self._on_item_clicked(item, 0)
                return
        super().keyPressEvent(event)

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        path = item.data(0, self._PATH_ROLE)
        if not path:
            return
        if item.data(0, self._LOADED_ROLE):
            return
        self._populate_children(item, path)

    def _on_context_menu(self, pos) -> None:
        item = self.itemAt(pos)
        menu = QMenu(self)

        if item is None:
            menu.addAction("Connect Network Share...", self._connect_callback)
            menu.addAction("Refresh", self.refresh_shares)
        else:
            path = item.data(0, self._PATH_ROLE)
            is_share_root = bool(item.data(0, self._IS_SHARE_ROLE))
            if path:
                menu.addAction("Browse", lambda p=path: self.navigate_requested.emit(p))
            if is_share_root:
                source_url = item.data(0, self._SOURCE_URL_ROLE) or ""
                menu.addSeparator()
                menu.addAction(
                    "Edit Connection Parameters...",
                    lambda p=path, s=source_url: self.edit_connection_requested.emit(p, s),
                )
                menu.addAction("Disconnect", lambda i=item: self._on_disconnect(i))
            menu.addSeparator()
            menu.addAction("Connect Network Share...", self._connect_callback)
            menu.addAction("Refresh", self.refresh_shares)

        menu.exec(self.viewport().mapToGlobal(pos))

    def _on_disconnect(self, item: QTreeWidgetItem) -> None:
        path = item.data(0, self._PATH_ROLE)
        if path:
            unmount_share(path)
        self.refresh_shares()

    @staticmethod
    def _has_subdirectories(path: str) -> bool:
        try:
            for entry in os.scandir(path):
                if entry.is_dir(follow_symlinks=False) and not entry.name.startswith('.'):
                    return True
        except OSError:
            return False
        return False

    @staticmethod
    def _add_placeholder_child(parent: QTreeWidgetItem) -> None:
        placeholder = QTreeWidgetItem(parent, [""])
        placeholder.setData(0, Qt.UserRole, "__placeholder__")
        placeholder.setFlags(Qt.NoItemFlags)

    def _populate_children(self, parent: QTreeWidgetItem, parent_path: str) -> None:
        parent.takeChildren()
        try:
            dir_entries = sorted(
                (
                    entry
                    for entry in os.scandir(parent_path)
                    if entry.is_dir(follow_symlinks=False) and not entry.name.startswith('.')
                ),
                key=lambda e: e.name.lower(),
            )
        except OSError:
            parent.setData(0, self._LOADED_ROLE, True)
            return

        for entry in dir_entries:
            child_path = entry.path
            child = QTreeWidgetItem(parent, [entry.name])
            child.setData(0, self._PATH_ROLE, child_path)
            child.setData(0, self._IS_SHARE_ROLE, False)
            child.setData(0, self._LOADED_ROLE, False)
            child.setIcon(0, self._folder_icon)
            child.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if self._has_subdirectories(child_path):
                self._add_placeholder_child(child)

        parent.setData(0, self._LOADED_ROLE, True)
