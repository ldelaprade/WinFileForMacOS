from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from urllib.parse import unquote, urlparse

from PySide6.QtCore import QMimeData, QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QTreeWidget, QTreeWidgetItem, QWidget, QFileIconProvider

from .ui_theme import XPIconProvider


_WINDOWS_KNOWN_SHARES_KEY = "network/known_windows_shares"


def get_mounted_network_shares() -> list[tuple[str, str, str]]:
    """Return (display_name, mount_path, source_url) for each mounted network share."""
    if os.name == "nt":
        return _get_windows_network_shares()

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


def normalize_network_share_input(raw_input: str) -> str:
    """Normalize SMB/UNC text input, especially permissive Windows SMB variants."""
    raw = raw_input.strip()
    if not raw:
        return raw

    if os.name != "nt":
        return raw

    # Accept user input like smb:\\host\share or smb://host/share by converting to UNC.
    if raw.lower().startswith("smb:"):
        rest = raw[4:].lstrip("/\\")
        if not rest:
            return raw
        parts = [segment for segment in re.split(r"[\\/]+", rest) if segment]
        if len(parts) >= 2:
            host = parts[0]
            share_name = parts[1]
            unc = f"\\\\{host}\\{share_name}"
            if len(parts) > 2:
                suffix = "\\".join(parts[2:])
                unc = f"{unc}\\{suffix}"
            return unc
        return raw

    if raw.startswith("\\\\"):
        parts = [segment for segment in re.split(r"[\\/]+", raw) if segment]
        if len(parts) >= 2:
            unc = f"\\\\{parts[0]}\\{parts[1]}"
            if len(parts) > 2:
                suffix = "\\".join(parts[2:])
                unc = f"{unc}\\{suffix}"
            return unc
    return raw


def _get_windows_network_shares() -> list[tuple[str, str, str]]:
    """Return active Windows network shares from `net use` output."""
    try:
        result = subprocess.run(
            ["net", "use"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except OSError:
        return []

    if result.returncode != 0:
        return []

    shares: list[tuple[str, str, str]] = []
    seen_paths: set[str] = set()

    # Example lines include mapped and unmapped entries, e.g.:
    # OK           Z:        \\server\share          Microsoft Windows Network
    # OK                     \\server\share          Microsoft Windows Network
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("-"):
            continue
        if "\\\\" not in line:
            continue
        if "command completed" in line.lower():
            continue

        match = re.search(r"(\\\\[^\s]+)", line)
        if match is None:
            continue
        remote_path = match.group(1).rstrip("\\")
        if not remote_path:
            continue

        normalized = remote_path.lower()
        if normalized in seen_paths:
            continue
        seen_paths.add(normalized)

        parts = [segment for segment in remote_path.split("\\") if segment]
        display = parts[1] if len(parts) >= 2 else remote_path
        shares.append((display, remote_path, remote_path))

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
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["net", "use", mount_path, "/delete", "/y"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            return result.returncode == 0
        except OSError:
            return False

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
    """Parse network share input into (mount_root, target_path).

    macOS/Linux: supports smb://host/share[/sub/path].
    Windows: supports UNC paths (\\\\host\\share[\\sub\\path]) and smb:// URLs.

    Returns (None, None) for invalid input.
    """
    raw = normalize_network_share_input(smb_url)
    if not raw:
        return None, None

    # On Windows, access SMB shares via UNC paths.
    if os.name == "nt":
        if raw.startswith("\\\\"):
            parts = [segment for segment in raw.split("\\") if segment]
            if len(parts) < 2:
                return None, None
            host = parts[0]
            share_name = parts[1]
            mount_root = f"\\\\{host}\\{share_name}"
            if len(parts) == 2:
                return mount_root, mount_root
            sub_path = "\\".join(parts[2:])
            return mount_root, f"{mount_root}\\{sub_path}"

        parsed = urlparse(raw)
        if parsed.scheme.lower() != "smb" or not parsed.netloc:
            return None, None
        path_parts = [unquote(p) for p in parsed.path.split("/") if p]
        if not path_parts:
            return None, None
        host = parsed.hostname or parsed.netloc
        share_name = path_parts[0]
        mount_root = f"\\\\{host}\\{share_name}"
        if len(path_parts) == 1:
            return mount_root, mount_root
        sub_path = "\\".join(path_parts[1:])
        return mount_root, f"{mount_root}\\{sub_path}"

    parsed = urlparse(raw)
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
    add_to_favorites_requested: Signal = Signal(str)
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
        self._settings = QSettings("WinFileXP", "WinFileXP")
        self._known_windows_shares = self._load_known_windows_shares()
        style = QApplication.style()
        self._network_root_icon = style.standardIcon(QStyle.SP_DriveNetIcon)
        icon_provider = XPIconProvider()
        self._folder_icon = icon_provider.icon(QFileIconProvider.Folder)

        self.setHeaderHidden(True)
        self.setRootIsDecorated(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QTreeWidget.DragOnly)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.itemClicked.connect(self._on_item_clicked)
        self.itemExpanded.connect(self._on_item_expanded)

        self.refresh_shares()

    def refresh_shares(self) -> None:
        """Re-scan mounted network shares and repopulate the tree."""
        self.clear()
        shares = list(get_mounted_network_shares())
        if os.name == "nt":
            shares.extend(self._known_windows_share_entries())

        seen_paths: set[str] = set()
        for display_name, mount_path, source_url in shares:
            normalized = mount_path.lower()
            if normalized in seen_paths:
                continue
            seen_paths.add(normalized)
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
                menu.addAction("Add to Favorites", lambda p=path: self.add_to_favorites_requested.emit(p))
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
            self.unregister_known_windows_share(path)
            unmount_share(path)
        self.refresh_shares()

    def mimeData(self, items: list[QTreeWidgetItem]) -> QMimeData:
        mime_data = QMimeData()
        urls: list[QUrl] = []

        for item in items:
            path = item.data(0, self._PATH_ROLE)
            if not isinstance(path, str) or not path:
                continue
            if not os.path.exists(path):
                continue
            urls.append(QUrl.fromLocalFile(path))

        if not urls:
            return mime_data

        mime_data.setUrls(urls)
        uri_list = "\r\n".join(url.toString(QUrl.FullyEncoded) for url in urls) + "\r\n"
        mime_data.setData("text/uri-list", uri_list.encode("utf-8"))
        return mime_data

    def register_known_windows_share(self, mount_path: str) -> None:
        if os.name != "nt":
            return
        normalized = normalize_network_share_input(mount_path)
        if not normalized.startswith("\\\\"):
            return
        key = normalized.lower()
        if key in {entry.lower() for entry in self._known_windows_shares}:
            return
        self._known_windows_shares.append(normalized)
        self._save_known_windows_shares()

    def unregister_known_windows_share(self, mount_path: str) -> None:
        if os.name != "nt":
            return
        normalized = normalize_network_share_input(mount_path).lower()
        before = len(self._known_windows_shares)
        self._known_windows_shares = [
            entry for entry in self._known_windows_shares if entry.lower() != normalized
        ]
        if len(self._known_windows_shares) != before:
            self._save_known_windows_shares()

    def _known_windows_share_entries(self) -> list[tuple[str, str, str]]:
        entries: list[tuple[str, str, str]] = []
        reachable: list[str] = []
        for known_share in self._known_windows_shares:
            normalized = normalize_network_share_input(known_share)
            if not normalized.startswith("\\\\"):
                continue
            # Keep shortcuts useful by showing currently reachable shares.
            if not os.path.isdir(normalized):
                continue
            parts = [segment for segment in normalized.split("\\") if segment]
            display = parts[1] if len(parts) >= 2 else normalized
            entries.append((display, normalized, normalized))
            reachable.append(normalized)
        if reachable != self._known_windows_shares:
            self._known_windows_shares = reachable
            self._save_known_windows_shares()
        return entries

    def _load_known_windows_shares(self) -> list[str]:
        if os.name != "nt":
            return []
        saved = self._settings.value(_WINDOWS_KNOWN_SHARES_KEY, [])
        if isinstance(saved, str):
            saved = [saved]
        if not isinstance(saved, list):
            return []
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in saved:
            if not isinstance(value, str):
                continue
            normalized = normalize_network_share_input(value)
            if not normalized.startswith("\\\\"):
                continue
            lowered = normalized.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            cleaned.append(normalized)
        return cleaned

    def _save_known_windows_shares(self) -> None:
        if os.name != "nt":
            return
        self._settings.setValue(_WINDOWS_KNOWN_SHARES_KEY, self._known_windows_shares)

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
