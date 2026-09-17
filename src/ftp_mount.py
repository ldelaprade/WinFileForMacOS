from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path


def curlftpfs_executable() -> str | None:
    return shutil.which("curlftpfs")


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

    credentials = f"{username}:{password}@" if username else ""
    remote = f"ftp://{credentials}{server}:{port}{remote_path}"

    return [
        executable,
        "-o",
        "reconnect,ftp_port=-",
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
        return None, mount_path, "curlftpfs was not found. Install macFUSE and curlftpfs first."

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
            stderr=subprocess.DEVNULL,
        )
        return process, mount_path, None
    except OSError as error:
        return None, mount_path, str(error)


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
