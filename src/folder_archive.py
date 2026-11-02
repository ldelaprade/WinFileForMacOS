from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Iterator


PACK_FORMATS = {
    "zip": ("ZIP (.zip)", ".zip", "ZIP archives (*.zip)"),
    "gzip": ("GZIP (.tar.gz)", ".tar.gz", "Gzipped TAR archives (*.tar.gz)"),
    "tar": ("TAR (.tar)", ".tar", "TAR archives (*.tar)"),
    "iso": ("ISO (.iso)", ".iso", "ISO images (*.iso)"),
}


class PackError(Exception):
    pass


def find_mkisofs() -> str | None:
    executable = shutil.which("mkisofs")
    if executable:
        return executable
    if sys.platform == "darwin":
        for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
            candidate = Path(prefix) / "mkisofs"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None


def validate_destination(source: Path, destination: Path) -> None:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_dir():
        raise PackError("The source folder is no longer available.")
    if (destination == source or source in destination.parents
            or any(parent.is_dir() and parent.samefile(source)
                   for parent in destination.parents)):
        raise PackError("Save the archive outside the source folder or drive.")
    if not destination.parent.is_dir():
        raise PackError("The output folder does not exist.")
    if destination.exists() and not destination.is_file():
        raise PackError("The output path is not a regular file.")


def _entries(root: Path) -> Iterator[Path]:
    def on_error(error: OSError) -> None:
        raise error

    for directory, folders, files in os.walk(root, followlinks=False, onerror=on_error):
        for name in sorted(folders + files):
            path = Path(directory) / name
            mode = path.lstat().st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
                raise PackError(f"Cannot pack a special filesystem entry:\n{path}")
            yield path


def _create_iso(root: Path, output: Path, executable: str) -> None:
    arguments = [
        executable, "-o", str(output), "-iso-level", "3",
        "-R", "-J", "-joliet-long", "-V", "ARCHIVE", str(root),
    ]
    with tempfile.TemporaryFile() as log:
        result = subprocess.run(
            arguments, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        )
        if result.returncode:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - 8000))
            detail = log.read().decode("utf-8", errors="replace")
            raise PackError(f"mkisofs failed (exit {result.returncode}):\n{detail}")
    with output.open("rb") as image:
        image.seek(16 * 2048)
        if image.read(7) != b"\x01CD001\x01":
            raise PackError("mkisofs did not produce a valid data ISO.")


def pack_folder(
    source: str, destination: str, archive_format: str, executable: str | None = None,
) -> str:
    temporary: Path | None = None
    try:
        root = Path(source).resolve()
        output = Path(destination).absolute()
        if archive_format not in PACK_FORMATS:
            raise PackError(f"Unsupported archive format: {archive_format}")
        validate_destination(root, output)
        if archive_format == "iso" and not executable:
            raise PackError("Install cdrtools/mkisofs to create an ISO.")
        with tempfile.NamedTemporaryFile(
            prefix=".retrofile-pack-", dir=output.parent, delete=False,
        ) as file:
            temporary = Path(file.name)
        if archive_format == "zip":
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED,
                                 strict_timestamps=False) as zip_archive:
                for path in _entries(root):
                    name = path.relative_to(root).as_posix()
                    if path.is_symlink():
                        info = zipfile.ZipInfo(name)
                        info.create_system = 3
                        info.external_attr = path.lstat().st_mode << 16
                        zip_archive.writestr(info, os.fsencode(os.readlink(path)))
                    else:
                        zip_archive.write(path, name)
        elif archive_format in ("tar", "gzip"):
            mode = "w:gz" if archive_format == "gzip" else "w"
            with tarfile.open(temporary, mode, dereference=False) as tar_archive:
                for path in _entries(root):
                    tar_archive.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
        else:
            if executable is None:
                raise PackError("Install cdrtools/mkisofs to create an ISO.")
            _create_iso(root, temporary, executable)
        os.replace(temporary, output)
        temporary = None
        return str(output)
    except (OSError, tarfile.TarError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise PackError(f"Could not pack the folder:\n{error}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
