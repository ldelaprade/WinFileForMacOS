from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path


def sshfs_executable() -> str | None:
    return shutil.which("sshfs")


def mount_path_for(server: str, port: int, folder: str, username: str) -> Path:
    identity = f"{username}@{server}:{port}:{folder}".encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()[:16]
    return Path.home() / ".wfcache" / "ssh_mounts" / digest


def build_sshfs_command(
    executable: str,
    server: str,
    port: int,
    folder: str,
    username: str,
    mount_path: Path,
    has_password: bool,
) -> list[str]:
    remote_path = folder.strip() or "/"
    if not remote_path.startswith("/"):
        remote_path = f"/{remote_path}"
    remote = f"{username}@{server}:{remote_path}" if username else f"{server}:{remote_path}"

    command = [
        executable,
        "-f",
        "-p",
        str(port),
        "-o",
        "reconnect",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
    ]
    if has_password:
        command.extend(["-o", "password_stdin"])
    command.extend([remote, str(mount_path)])
    return command


def start_ssh_mount(
    server: str,
    port: int,
    folder: str,
    username: str,
    password: str,
) -> tuple[subprocess.Popen[bytes] | None, Path, str | None]:
    executable = sshfs_executable()
    mount_path = mount_path_for(server, port, folder, username)
    if executable is None:
        return None, mount_path, "sshfs was not found. Install macFUSE and SSHFS first."

    try:
        mount_path.mkdir(parents=True, exist_ok=True)
        command = build_sshfs_command(
            executable,
            server,
            port,
            folder,
            username,
            mount_path,
            bool(password),
        )
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE if password else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if password and process.stdin is not None:
            process.stdin.write(password.encode("utf-8") + b"\n")
            process.stdin.close()
        return process, mount_path, None
    except OSError as error:
        return None, mount_path, str(error)


def unmount_ssh_path(mount_path: str) -> bool:
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