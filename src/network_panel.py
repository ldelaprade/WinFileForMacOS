from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from urllib.parse import unquote, urlparse

from PySide6.QtCore import QMimeData, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QTreeWidget, QTreeWidgetItem, QWidget, QFileIconProvider

from .ui_theme import XPIconProvider
from .ssh_mount import unmount_ssh_path
from .ftp_mount import active_windows_ftp_mounts, is_windows_ftp_mount, unmount_ftp_path


_WINDOWS_KNOWN_SHARES_KEY = "network/known_windows_shares"
NETWORK_PROBE_TIMEOUT_MS = 3000
_UNREACHABLE_SECONDS = 30.0
_unreachable_until: dict[str, float] = {}
_posix_network_mounts: list[str] = []


def is_network_path(path: str) -> bool:
    """True for UNC paths, mapped network drives and known network mounts (no I/O on the share)."""
    if not path:
        return False
    if os.name == "nt":
        if path.startswith(("\\\\", "//")):
            return True
        drive = os.path.splitdrive(path)[0]
        if len(drive) != 2 or drive[1] != ":":
            return False
        import ctypes

        return ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\") == 4  # DRIVE_REMOTE
    return network_root(path) is not None


def network_root(path: str) -> str | None:
    """Share identity used to remember unreachable locations."""
    if os.name == "nt":
        normalized = path.replace("/", "\\")
        if normalized.startswith("\\\\"):
            parts = [segment for segment in normalized.split("\\") if segment]
            return ("\\\\" + "\\".join(parts[:2])).lower() if parts else None
        drive = os.path.splitdrive(normalized)[0]
        return drive.upper() or None
    absolute = os.path.abspath(path)
    for mount in _posix_network_mounts:
        if absolute == mount or absolute.startswith(mount.rstrip("/") + "/"):
            return mount
    return None


def mark_unreachable(path: str) -> None:
    root = network_root(path)
    if root:
        _unreachable_until[root] = time.monotonic() + _UNREACHABLE_SECONDS


def clear_unreachable(path: str) -> None:
    root = network_root(path)
    if root:
        _unreachable_until.pop(root, None)


def is_marked_unreachable(path: str) -> bool:
    root = network_root(path)
    return bool(root) and _unreachable_until.get(root, 0.0) > time.monotonic()


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
        if not any(
            t in lower
            for t in ("smbfs", "nfs", "afpfs", "cifs", "webdav", "fuse", "sshfs", "curlftpfs", "ftp")
        ):
            continue
        source, rest = line.split(" on ", 1)
        mount_path = rest.strip().split(" ")[0]
        source_url = _mounted_source_to_url(source.strip(), lower)
        display = _network_share_display_name(mount_path, source_url)
        shares.append((display, mount_path, source_url))
    _posix_network_mounts[:] = [os.path.abspath(mount) for _d, mount, _s in shares]
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
    """Return Windows mapped volumes, additional `net use` shares, and rclone FTP mounts."""
    shares: list[tuple[str, str, str]] = []
    seen_paths: set[str] = set()

    for mount_path, source_url in active_windows_ftp_mounts():
        display = _network_share_display_name(mount_path, source_url)
        shares.append((display, mount_path, source_url))
        seen_paths.add(mount_path.lower())

    for root_path, remote_path in _windows_mapped_network_drives():
        normalized = remote_path.lower()
        if normalized in seen_paths:
            continue
        seen_paths.add(normalized)
        parts = [segment for segment in remote_path.split("\\") if segment]
        display = parts[1] if len(parts) >= 2 else remote_path
        shares.append((display, root_path, remote_path))

    try:
        result = subprocess.run(
            ["net", "use"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except OSError:
        return shares

    if result.returncode != 0:
        return shares

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
        local_match = re.search(r"\b[A-Za-z]:\s*$", line[:match.start()])
        mount_path = local_match.group(0).strip() + "/" if local_match else remote_path
        shares.append((display, mount_path, remote_path))

    return shares


def _windows_mapped_network_drives() -> list[tuple[str, str]]:
    """Return (root_path, unc) for mapped drive letters without contacting the servers."""
    import ctypes

    drive_remote = 4
    error_connection_unavail = 1201
    kernel32 = ctypes.windll.kernel32
    try:
        mpr = ctypes.windll.mpr
    except OSError:
        return []
    mask = kernel32.GetLogicalDrives()
    drives: list[tuple[str, str]] = []
    for index in range(26):
        if not mask & (1 << index):
            continue
        letter = chr(ord("A") + index)
        if kernel32.GetDriveTypeW(f"{letter}:\\") != drive_remote:
            continue
        buffer = ctypes.create_unicode_buffer(1024)
        length = ctypes.c_ulong(len(buffer))
        result = mpr.WNetGetConnectionW(f"{letter}:", buffer, ctypes.byref(length))
        if result not in (0, error_connection_unavail):
            continue
        remote_path = buffer.value.rstrip("\\")
        if remote_path.startswith("\\\\") and not remote_path.startswith("\\\\?\\"):
            drives.append((f"{letter}:/", remote_path))
    return drives


def _mounted_source_to_url(source: str, lower_mount_line: str) -> str:
    if "curlftpfs" in lower_mount_line or source.lower().startswith("curlftpfs#"):
        ftp_source = source.split("#", 1)[-1]
        return ftp_source if "://" in ftp_source else f"ftp://{ftp_source}"

    if "fuse" in lower_mount_line or "sshfs" in lower_mount_line:
        return f"ssh://{source}"

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


def _network_share_display_name(mount_path: str, source_url: str) -> str:
    parsed = urlparse(source_url)
    if parsed.scheme in {"ftp", "ftps"} and parsed.hostname:
        location = parsed.hostname
        if parsed.port and parsed.port != 21:
            location = f"{location}:{parsed.port}"
        folder = unquote(parsed.path).strip("/")
        return f"FTP: {location}/{folder}" if folder else f"FTP: {location}"
    return mount_path.rsplit("/", 1)[-1] or mount_path


def mount_smb_share(smb_url: str) -> bool:
    """Trigger macOS to mount an SMB share via AppleScript — no Finder window."""
    applescript_url = smb_url.replace("\\", "\\\\").replace('"', '\\"')
    try:
        subprocess.Popen(
            ["osascript", "-e", f'mount volume "{applescript_url}"'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except OSError:
        return False


def unmount_share(mount_path: str) -> bool:
    """Unmount a network share by its local mount path."""
    if "ssh_mounts" in mount_path:
        return unmount_ssh_path(mount_path)
    if "ftp_mounts" in mount_path or is_windows_ftp_mount(mount_path):
        return unmount_ftp_path(mount_path)

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
    _children_listed: Signal = Signal(str, object)
    _shares_listed: Signal = Signal(int, object)
    _PATH_ROLE = Qt.UserRole
    _IS_SHARE_ROLE = Qt.UserRole + 1
    _LOADED_ROLE = Qt.UserRole + 2
    _SOURCE_URL_ROLE = Qt.UserRole + 3

    def __init__(
        self,
        connect_callback: Callable[[], None],
        ssh_callback: Callable[[], None],
        ftp_callback: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._connect_callback = connect_callback
        self._ssh_callback = ssh_callback
        self._ftp_callback = ftp_callback
        self._settings = QSettings("WinFileXP", "WinFileXP")
        self._known_windows_shares = self._load_known_windows_shares()
        style = QApplication.style()
        self._network_root_icon = style.standardIcon(QStyle.SP_DriveNetIcon)
        icon_provider = XPIconProvider()
        self._folder_icon = icon_provider.icon(QFileIconProvider.Folder)
        self._pending_expansions: dict[str, QTreeWidgetItem] = {}
        self._children_listed.connect(self._on_children_listed)

        self.setHeaderHidden(True)
        self.setRootIsDecorated(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QTreeWidget.DragOnly)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.itemClicked.connect(self._on_item_clicked)
        self.itemExpanded.connect(self._on_item_expanded)
        self._refresh_token = 0
        self._shares_listed.connect(self._on_shares_listed)

        self.refresh_shares()

    def refresh_shares(self) -> None:
        """Re-scan mounted network shares in the background and repopulate the tree."""
        self._refresh_token += 1
        token = self._refresh_token

        def enumerate_shares() -> None:
            try:
                shares = list(get_mounted_network_shares())
            except Exception:
                shares = []
            self._shares_listed.emit(token, shares)

        # Querying mapped drives can block when the redirector is unhappy.
        threading.Thread(target=enumerate_shares, daemon=True).start()

    def _on_shares_listed(self, token: int, shares: list[tuple[str, str, str]]) -> None:
        if token != self._refresh_token:
            return
        self._pending_expansions.clear()
        self.clear()
        if os.name == "nt":
            shares.extend(self._known_windows_share_entries())

        seen_paths: set[str] = set()
        for display_name, mount_path, source_url in shares:
            normalized = (
                source_url.rstrip("\\/").lower()
                if os.name == "nt" and source_url.startswith("\\\\")
                else mount_path.lower()
            )
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
            # Shares are only accessed on demand (expand/click), never while listing.
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
        if not path or item.data(0, self._LOADED_ROLE) or path in self._pending_expansions:
            return
        item.takeChildren()
        if is_marked_unreachable(path):
            self._set_status_child(item, "Not reachable")
            return
        self._set_status_child(item, "Loading...")
        self._pending_expansions[path] = item
        threading.Thread(target=self._list_children, args=(path,), daemon=True).start()
        QTimer.singleShot(NETWORK_PROBE_TIMEOUT_MS, lambda p=path: self._on_expansion_timeout(p))

    def _list_children(self, path: str) -> None:
        try:
            with os.scandir(path) as entries:
                names = sorted(
                    (
                        entry.name
                        for entry in entries
                        if entry.is_dir(follow_symlinks=False) and not entry.name.startswith(".")
                    ),
                    key=str.lower,
                )
        except OSError:
            names = None
        self._children_listed.emit(path, names)

    def _on_expansion_timeout(self, path: str) -> None:
        item = self._pending_expansions.pop(path, None)
        if item is None:
            return
        mark_unreachable(path)
        item.takeChildren()
        self._set_status_child(item, "Not reachable")

    def _on_children_listed(self, path: str, names: list[str] | None) -> None:
        item = self._pending_expansions.pop(path, None)
        if item is None:
            return
        item.takeChildren()
        if names is None:
            mark_unreachable(path)
            self._set_status_child(item, "Not reachable")
            return
        clear_unreachable(path)
        for name in names:
            child = QTreeWidgetItem(item, [name])
            child.setData(0, self._PATH_ROLE, os.path.join(path, name))
            child.setData(0, self._IS_SHARE_ROLE, False)
            child.setData(0, self._LOADED_ROLE, False)
            child.setIcon(0, self._folder_icon)
            child.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self._add_placeholder_child(child)
        item.setData(0, self._LOADED_ROLE, True)

    @staticmethod
    def _set_status_child(parent: QTreeWidgetItem, text: str) -> None:
        status = QTreeWidgetItem(parent, [text])
        status.setData(0, Qt.UserRole, "__placeholder__")
        status.setFlags(Qt.NoItemFlags)

    def _on_context_menu(self, pos) -> None:
        item = self.itemAt(pos)
        menu = QMenu(self)
        add_location_menu = QMenu("Add Network Location", menu)
        add_location_menu.addAction("Windows Share (SMB)...", self._connect_callback)
        add_location_menu.addAction("SSH / SFTP...", self._ssh_callback)
        add_location_menu.addAction("FTP...", self._ftp_callback)

        if item is None:
            menu.addMenu(add_location_menu)
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
            menu.addMenu(add_location_menu)
            menu.addAction("Refresh", self.refresh_shares)

        menu.exec(self.viewport().mapToGlobal(pos))

    def _on_disconnect(self, item: QTreeWidgetItem) -> None:
        path = item.data(0, self._PATH_ROLE)
        if path:
            source_url = item.data(0, self._SOURCE_URL_ROLE) or path
            self.unregister_known_windows_share(source_url)
            unmount_share(path)
        self.refresh_shares()

    def mimeData(self, items: list[QTreeWidgetItem]) -> QMimeData:
        mime_data = QMimeData()
        urls: list[QUrl] = []

        for item in items:
            path = item.data(0, self._PATH_ROLE)
            if not isinstance(path, str) or not path:
                continue
            if not is_network_path(path) and not os.path.exists(path):
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
        for known_share in self._known_windows_shares:
            normalized = normalize_network_share_input(known_share)
            if not normalized.startswith("\\\\"):
                continue
            parts = [segment for segment in normalized.split("\\") if segment]
            display = parts[1] if len(parts) >= 2 else normalized
            entries.append((display, normalized, normalized))
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
    def _add_placeholder_child(parent: QTreeWidgetItem) -> None:
        placeholder = QTreeWidgetItem(parent, [""])
        placeholder.setData(0, Qt.UserRole, "__placeholder__")
        placeholder.setFlags(Qt.NoItemFlags)
