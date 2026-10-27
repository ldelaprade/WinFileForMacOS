"""Create AIRS-400 / Vision 1000 style VFS images (APVDF000.vfs) from a folder.

Python port of Appareo.VFS.Test.v1.ImageCreator.CreateImage (v1 SD-card layout):

    [SDConfig, 64 KB]
    per file: [StreamStart + data, padded to alignment]
              [StreamEnd, padded to alignment]
              [StreamEnd (copy), padded to alignment]
"""

from __future__ import annotations

import hashlib
import os
import re
import struct
from pathlib import Path
from typing import Callable, Iterable, List, Optional

DEFAULT_VFS_NAME = "APVDF000.vfs"
DEFAULT_ALIGNMENT = 512

CONFIG_SIZE = 64 * 1024
STREAM_START_MAGIC = 0x42FF42FF
STREAM_END_MAGIC = 0x43FF42FF
STREAM_START_SIZE = 68
STREAM_END_SIZE = 28
FILE_NAME_FIELD = 32  # includes the NUL terminator
MAX_SECTIONS = 255  # SectionCount is a single byte in the config
MAX_FILE_SIZE = 0xFFFFFFFF - STREAM_START_SIZE  # StreamLength is a UInt32

_VISION_NAME = re.compile(r"^VIS-[A-Za-z0-9]{4,6}-\d{1,5}-\d{1,3}\.[A-Za-z0-9]+$")

ProgressCallback = Callable[[int, int], None]


class VfsError(Exception):
    """Raised when a VFS image cannot be created."""


def is_vision_file_name(name: str) -> bool:
    """True for names such as VIS-000001-37749-001.img or VIS-FF1N-02241-001.aud."""
    return bool(_VISION_NAME.match(name))


def find_recording_files(folder: os.PathLike | str) -> List[Path]:
    """Return the Vision/AIRS recording files directly inside `folder`, sorted by name."""
    root = Path(folder)
    if not root.is_dir():
        return []
    return sorted(
        (p for p in root.iterdir() if p.is_file() and is_vision_file_name(p.name)),
        key=lambda p: p.name.upper(),
    )


def folder_has_recordings(folder: os.PathLike | str) -> bool:
    """Cheap check intended for a context-menu 'is this folder eligible' test."""
    root = Path(folder)
    try:
        with os.scandir(root) as it:
            return any(e.is_file() and is_vision_file_name(e.name) for e in it)
    except OSError:
        return False


def fletcher16(data: bytes) -> int:
    """Fletcher checksum used by the firmware: (sum2 << 16) | sum1 over LE 16-bit words."""
    if len(data) % 2:
        raise ValueError("Fletcher16 requires an even number of bytes")
    sum1 = sum2 = 0
    for (word,) in struct.iter_unpack("<H", data):
        sum1 = (sum1 + word) % 0xFFFF
        sum2 = (sum2 + sum1) % 0xFFFF
    return (sum2 << 16) | sum1


def _align(size: int, boundary: int) -> int:
    return -(-size // boundary) * boundary


def _stream_start(uuid: bytes, name: str) -> bytes:
    try:
        raw_name = name.encode("ascii")
    except UnicodeEncodeError as ex:
        raise VfsError(f"File name must be ASCII: {name!r}") from ex
    if len(raw_name) >= FILE_NAME_FIELD:
        raise VfsError(f"File name too long for VFS (max {FILE_NAME_FIELD - 1} chars): {name!r}")
    body = struct.pack(
        "<I16s32sIH6s",
        STREAM_START_MAGIC,
        uuid,
        raw_name,  # struct pads with NULs
        0,  # CardTripNumber
        0,  # Attributes
        b"\0" * 6,
    )
    return body + struct.pack("<I", fletcher16(body))


def _stream_end(uuid: bytes, stream_length: int) -> bytes:
    body = struct.pack("<I16sI", STREAM_END_MAGIC, uuid, stream_length)
    return body + struct.pack("<I", fletcher16(body))


def _sd_config(sizes: List[int], alignment: int) -> bytes:
    payload = struct.pack("<BBBIBII", 1, 0, 0, 0, len(sizes), alignment, 0)
    offset = 0
    for file_size in sizes:
        section_size = (
            _align(STREAM_START_SIZE + file_size, alignment)
            + 2 * _align(STREAM_END_SIZE, alignment)
        )
        payload += struct.pack("<QQQQ", offset, section_size, 1, 0)
        offset += section_size
    payload += hashlib.md5(payload).digest()
    return payload.ljust(CONFIG_SIZE, b"\0")


def create_vfs_image(
    source_files: Iterable[os.PathLike | str],
    output_file: os.PathLike | str,
    alignment: int = DEFAULT_ALIGNMENT,
    progress: Optional[ProgressCallback] = None,
) -> Path:
    """Write a VFS image containing `source_files` (in the given order).

    The image is built in a temporary file and moved into place when complete,
    so a failure never leaves a half-written APVDF000.vfs behind.
    `progress(bytes_done, bytes_total)` is called while data is copied.
    """
    files = [Path(f) for f in source_files]
    if alignment <= 0 or alignment % 512:
        raise VfsError("alignment must be a positive multiple of 512")
    if not files:
        raise VfsError("No files to add to the VFS")
    if len(files) > MAX_SECTIONS:
        raise VfsError(f"Too many files ({len(files)}); the VFS format supports at most {MAX_SECTIONS}")
    names = [f.name.upper() for f in files]
    if len(set(names)) != len(names):
        raise VfsError("Duplicate file names are not allowed")

    sizes = []
    for f in files:
        if not f.is_file():
            raise VfsError(f"Source file not found: {f}")
        size = f.stat().st_size
        if size > MAX_FILE_SIZE:
            raise VfsError(f"File too large for VFS (4 GB limit): {f}")
        sizes.append(size)
    total = sum(sizes)

    output = Path(output_file)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(output.name + ".tmp")
    done = 0
    try:
        with open(tmp, "wb") as out:
            out.write(_sd_config(sizes, alignment))
            for index, (src, size) in enumerate(zip(files, sizes)):
                uuid = bytes(15) + bytes([index % 256])
                out.write(_stream_start(uuid, src.name))
                copied = 0
                with open(src, "rb") as inp:
                    while True:
                        chunk = inp.read(1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                        copied += len(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
                if copied != size:
                    raise VfsError(f"File changed while being read: {src}")
                raw = STREAM_START_SIZE + size
                out.write(bytes(_align(raw, alignment) - raw))
                end = _stream_end(uuid, raw)
                padding = bytes(_align(STREAM_END_SIZE, alignment) - STREAM_END_SIZE)
                out.write(end + padding)
                out.write(end + padding)
        os.replace(tmp, output)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return output


def create_vfs_from_folder(
    source_folder: os.PathLike | str,
    target_folder: os.PathLike | str,
    vfs_name: str = DEFAULT_VFS_NAME,
    alignment: int = DEFAULT_ALIGNMENT,
    progress: Optional[ProgressCallback] = None,
) -> Path:
    """Build `<target_folder>/APVDF000.vfs` from the recordings in `source_folder`."""
    files = find_recording_files(source_folder)
    if not files:
        raise VfsError(f"No Vision/AIRS recording files (VIS-*-*-*.*) in {source_folder}")
    target = Path(target_folder)
    if target.resolve() == Path(source_folder).resolve():
        raise VfsError("The target folder must differ from the source folder")
    return create_vfs_image(files, target / vfs_name, alignment, progress)


def read_vfs_index(vfs_file: os.PathLike | str) -> List[dict]:
    """Read back a VFS image and validate every checksum. Returns name/size per file."""
    entries = []
    with open(vfs_file, "rb") as f:
        config = f.read(CONFIG_SIZE)
        major, minor, rev, build, count, alignment, _ = struct.unpack_from("<BBBIBII", config)
        body_len = 16 + 32 * count
        if hashlib.md5(config[:body_len]).digest() != config[body_len:body_len + 16]:
            raise VfsError("SDConfig checksum mismatch")
        for i in range(count):
            offset, size, _, _ = struct.unpack_from("<QQQQ", config, 16 + 32 * i)
            f.seek(CONFIG_SIZE + offset)
            head = f.read(STREAM_START_SIZE)
            magic, uuid, name, _, _, _, checksum = struct.unpack("<I16s32sIH6sI", head)
            if magic != STREAM_START_MAGIC or checksum != fletcher16(head[:-4]):
                raise VfsError(f"Bad StreamStart in section {i}")
            f.seek(CONFIG_SIZE + offset + size - 2 * _align(STREAM_END_SIZE, alignment))
            end = f.read(STREAM_END_SIZE)
            emagic, euuid, length, echecksum = struct.unpack("<I16sII", end)
            if emagic != STREAM_END_MAGIC or echecksum != fletcher16(end[:-4]) or euuid != uuid:
                raise VfsError(f"Bad StreamEnd in section {i}")
            entries.append({"name": name.split(b"\0", 1)[0].decode("ascii"),
                            "size": length - STREAM_START_SIZE})
    return entries
