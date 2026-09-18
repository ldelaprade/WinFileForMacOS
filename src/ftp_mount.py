from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote


def curlftpfs_executable() -> str | None:
    return shutil.which("curlftpfs")


def missing_curlftpfs_message() -> str:
    if sys.platform.startswith("linux"):
        return "curlftpfs was not found. Install it with: sudo apt install curlftpfs"
    return "curlftpfs was not found. Install macFUSE and curlftpfs first."


def mount_path_for(server: str, port: int, folder: str, username: str) -> Path:
    identity = f"ftp://{username}@{server}:{port}:{folder}".encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()[:16]
    return Path.home() / ".wfcache" / "ftp_mounts" / digest


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


def start_ftp_mount(
    server: str,
    port: int,
    folder: str,
    username: str,
    password: str,
) -> tuple[subprocess.Popen[bytes] | None, Path, str | None]:
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


def unmount_ftp_path(mount_path: str) -> bool:
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
