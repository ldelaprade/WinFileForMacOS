"""Build an AIRS-400 / Vision 1000 VFS 'SD card' from a recordings folder and map it to a drive letter.

Typical use from a host application (e.g. a 'Mount as VFS' context-menu action):

    from airs_vfs import folder_has_recordings, mount_as_vfs

    if folder_has_recordings(folder):          # show the menu entry
        mount_as_vfs(folder, target_folder, "F", progress=update_bar)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .drive import (DriveError, available_drive_letters, list_substituted_drives,
                    mount_drive, unmount_drive)
from .vfs_image import (DEFAULT_VFS_NAME, ProgressCallback, VfsError, create_vfs_from_folder,
                        find_recording_files, folder_has_recordings, is_vision_file_name,
                        read_vfs_index)

__all__ = [
    "DEFAULT_VFS_NAME", "DriveError", "VfsError", "available_drive_letters",
    "create_vfs_from_folder", "find_recording_files", "folder_has_recordings",
    "is_vision_file_name", "list_substituted_drives", "mount_as_vfs", "mount_drive",
    "read_vfs_index", "unmount_drive",
]


def mount_as_vfs(
    source_folder: os.PathLike | str,
    target_folder: os.PathLike | str,
    drive_letter: str,
    replace_drive: bool = False,
    progress: Optional[ProgressCallback] = None,
) -> Path:
    """Create `<target_folder>/APVDF000.vfs` from `source_folder`, then subst `target_folder` to the drive.

    Returns the path of the VFS file. Raises VfsError or DriveError.
    The drive letter is validated first so no time is wasted building an image that cannot be mounted.
    """
    # Fail fast on an unusable drive letter before the (potentially long) image build.
    letter = drive_letter[:1].upper()
    mapped = list_substituted_drives()
    if letter in mapped and not replace_drive:
        raise DriveError(f"Drive {letter}: is already mapped to {mapped[letter]}")
    if letter not in mapped and os.path.exists(f"{letter}:\\"):
        raise DriveError(f"Drive {letter}: is already in use by a real drive")

    vfs = create_vfs_from_folder(source_folder, target_folder, progress=progress)
    mount_drive(target_folder, letter, replace=replace_drive)
    return vfs
