from __future__ import annotations

import hashlib
import os
import re
import shutil
import string
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

IS_WINDOWS = sys.platform == "win32"

# Windows has no FUSE/curlftpfs; mounts are done with rclone (+ WinFsp) onto a
# free drive letter instead. These dicts track state for the lifetime of the
# app so unmount_ftp_path() and the network panel can find them again.
_windows_ftp_processes: dict[str, subprocess.Popen[bytes]] = {}
_windows_ftp_configs: dict[str, Path] = {}
_windows_ftp_info: dict[str, dict[str, object]] = {}


def curlftpfs_executable() -> str | None:
    return shutil.which("curlftpfs")


def rclone_executable() -> str | None:
    return shutil.which("rclone")


def missing_curlftpfs_message() -> str:
    if IS_WINDOWS:
        return (
            "rclone was not found. Install rclone (https://rclone.org/downloads) "
            "and WinFsp (https://winfsp.dev), then try again."
        )
    if sys.platform.startswith("linux"):
        return "curlftpfs was not found. Install it with: sudo apt install curlftpfs"
    return "curlftpfs was not found. Install macFUSE and curlftpfs first."


def mount_path_for(server: str, port: int, folder: str, username: str) -> Path:
    identity = f"ftp://{username}@{server}:{port}:{folder}".encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()[:16]
    return Path.home() / ".wfcache" / "ftp_mounts" / digest


def _available_drive_letter() -> str | None:
    # Prefer high letters (Z: down to E:) so we don't collide with common drives.
    for letter in reversed(string.ascii_uppercase[4:]):
        if not Path(f"{letter}:\\").exists():
            return letter
    return None


# rclone's obscure output is always base64 (RawURLEncoding) of an AES-CTR
# blob: a 16-byte IV plus ciphertext, so it's never shorter than ~22 chars.
_OBSCURED_PASSWORD_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")


def _obscure_password(password: str, executable: str) -> tuple[str | None, str | None]:
    """Return (obscured_password, error). Never returns an unvalidated value."""
    try:
        result = subprocess.run(
            [executable, "obscure", "-q", password],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        return None, f"Could not obscure the FTP password with rclone: {error}"

    # rclone can print unrelated notices to stdout before the token, so only
    # trust the last non-blank line.
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    obscured = lines[-1] if lines else ""
    if not _OBSCURED_PASSWORD_RE.match(obscured):
        return None, "rclone returned an unexpected obscure result for the FTP password."
    return obscured, None


def _write_rclone_config(
    server: str, port: int, username: str, password: str, executable: str
) -> tuple[Path | None, str | None]:
    # rclone's ftp backend defaults an unset "user" to $USER (the local OS
    # account name), not "anonymous", so a blank username must be written
    # explicitly or rclone will try to log in as the wrong account.
    effective_username = username or "anonymous"

    # rclone's ftp backend unconditionally calls obscure.Reveal() on the
    # configured "pass" value (even when it's unset/empty), and Reveal()
    # errors on an empty string ("input too short when revealing password -
    # is it obscured?"). So an empty password must still be obscured and
    # written, or rclone refuses to mount at all.
    obscured_password, error = _obscure_password(password, executable)
    if error is not None:
        return None, error

    lines = [
        "[wf_ftp_mount]",
        "type = ftp",
        f"host = {server}",
        f"port = {port}",
        f"user = {effective_username}",
        f"pass = {obscured_password}",
    ]
    fd, path = tempfile.mkstemp(suffix=".conf", prefix="wf_ftp_")
    config_path = Path(path)
    with open(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return config_path, None


def build_rclone_mount_command(
    executable: str,
    config_path: Path,
    folder: str,
    mount_path: Path,
) -> list[str]:
    remote_path = folder.strip().lstrip("/")
    remote = f"wf_ftp_mount:{remote_path}" if remote_path else "wf_ftp_mount:"
    return [
        executable,
        "mount",
        remote,
        str(mount_path),
        "--config",
        str(config_path),
        "--vfs-cache-mode",
        "writes",
    ]


def build_curlftpfs_command(
    executable: str,
    server: str,
    port: int,
    folder: str,
    username: str,
    password: str,
    mount_path: Path,
) -> list[str]:
    remote_path = folder.strip()
    if remote_path and not remote_path.startswith("/"):
        remote_path = f"/{remote_path}"

    credentials = ""
    if username:
        credentials = f"{quote(username, safe='')}:{quote(password, safe='')}@"
    remote = f"ftp://{credentials}{server}:{port}{remote_path}"

    return [
        executable,
        remote,
        str(mount_path),
    ]


def _start_windows_ftp_mount(
    server: str,
    port: int,
    folder: str,
    username: str,
    password: str,
) -> tuple[subprocess.Popen[bytes] | None, Path, str | None]:
    executable = rclone_executable()
    if executable is None:
        return None, Path(), missing_curlftpfs_message()

    drive_letter = _available_drive_letter()
    if drive_letter is None:
        return None, Path(), "No free drive letter is available to mount the FTP location."

    mount_path = Path(f"{drive_letter}:\\")
    config_path, error = _write_rclone_config(server, port, username, password, executable)
    if error is not None or config_path is None:
        return None, mount_path, error

    try:
        command = build_rclone_mount_command(executable, config_path, folder, mount_path)
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except OSError as error:
        config_path.unlink(missing_ok=True)
        return None, mount_path, str(error)

    key = str(mount_path)
    _windows_ftp_processes[key] = process
    _windows_ftp_configs[key] = config_path
    _windows_ftp_info[key] = {"server": server, "port": port, "folder": folder, "username": username}
    return process, mount_path, None


def start_ftp_mount(
    server: str,
    port: int,
    folder: str,
    username: str,
    password: str,
) -> tuple[subprocess.Popen[bytes] | None, Path, str | None]:
    if IS_WINDOWS:
        return _start_windows_ftp_mount(server, port, folder, username, password)

    executable = curlftpfs_executable()
    mount_path = mount_path_for(server, port, folder, username)
    if executable is None:
        return None, mount_path, missing_curlftpfs_message()

    try:
        mount_path.mkdir(parents=True, exist_ok=True)
        command = build_curlftpfs_command(
            executable,
            server,
            port,
            folder,
            username,
            password,
            mount_path,
        )
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        return process, mount_path, None
    except OSError as error:
        return None, mount_path, str(error)


def ftp_mount_error(process: subprocess.Popen[bytes]) -> str | None:
    _, stderr = process.communicate()
    message = stderr.decode("utf-8", errors="replace").strip()
    return message or None


def is_windows_ftp_mount(mount_path: str) -> bool:
    return IS_WINDOWS and mount_path in _windows_ftp_processes


def is_path_on_windows_ftp_mount(path: str) -> bool:
    """True if `path` lives on a drive currently mounted via rclone/WinFsp.

    Shell icon/thumbnail extraction (SHGetFileInfo) on these virtual FTP
    drives can crash the Explorer COM Surrogate (dllhost.exe), so callers
    should avoid native icon lookups for paths on such mounts.
    """
    if not IS_WINDOWS or not _windows_ftp_processes:
        return False
    drive = os.path.splitdrive(path)[0].upper()
    return any(os.path.splitdrive(mount_path)[0].upper() == drive for mount_path in _windows_ftp_processes)


def is_ftp_mount_ready(mount_path: Path) -> bool:
    """Check whether a started FTP mount has actually attached to the filesystem.

    os.path.ismount() always returns True for a Windows drive root (e.g. "Z:\\")
    even if nothing is mounted there yet, so on Windows we instead check whether
    the rclone-mounted drive is now reachable.
    """
    if IS_WINDOWS:
        try:
            return mount_path.exists()
        except OSError:
            return False
    return os.path.ismount(mount_path)


def active_windows_ftp_mounts() -> list[tuple[str, str]]:
    """Return (mount_path, ftp_url) for every FTP location currently mounted via rclone."""
    shares: list[tuple[str, str]] = []
    for mount_path, info in _windows_ftp_info.items():
        server = info["server"]
        port = info["port"]
        folder = str(info["folder"]).strip("/")
        location = f"ftp://{server}:{port}/{folder}" if folder else f"ftp://{server}:{port}"
        shares.append((mount_path, location))
    return shares


def unmount_ftp_path(mount_path: str) -> bool:
    if IS_WINDOWS:
        process = _windows_ftp_processes.pop(mount_path, None)
        config_path = _windows_ftp_configs.pop(mount_path, None)
        _windows_ftp_info.pop(mount_path, None)
        if process is None:
            return False
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
        if config_path is not None:
            config_path.unlink(missing_ok=True)
        return True

    try:
        result = subprocess.run(
            ["umount", mount_path],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except OSError:
        return False

    if result.returncode == 0:
        return True

    try:
        result = subprocess.run(
            ["diskutil", "unmount", mount_path],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0
