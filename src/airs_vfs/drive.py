"""Map a folder to a drive letter with Windows `subst` (what the PTU functional tester does)."""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import string
import subprocess
from pathlib import Path
from typing import Dict, List

_LETTER = re.compile(r"^[A-Za-z]:?$")


class DriveError(Exception):
    """Raised when a drive cannot be mapped or unmapped."""


def _normalize(letter: str) -> str:
    if not isinstance(letter, str) or not _LETTER.match(letter):
        raise DriveError(f"Invalid drive letter: {letter!r}")
    return letter[0].upper()


def _subst(*args: str) -> subprocess.CompletedProcess:
    if os.name != "nt":
        raise DriveError("subst is only available on Windows")
    return subprocess.run(["subst", *args], capture_output=True, text=True, check=False)


def list_substituted_drives() -> Dict[str, str]:
    """Return {'F': 'C:\\\\Some\\\\Folder'} for drives currently created with subst."""
    result = _subst()
    drives: Dict[str, str] = {}
    for line in result.stdout.splitlines():
        match = re.match(r"^([A-Za-z]):\\: => (.+)$", line.strip())
        if match:
            target = match.group(2)
            if target.upper().startswith("UNC\\"):
                target = "\\" + target[3:]
            drives[match.group(1).upper()] = target
    return drives


def available_drive_letters() -> List[str]:
    """Letters that are not in use, suitable for populating a dialog combo box."""
    if os.name == "nt":
        drive_mask = ctypes.windll.kernel32.GetLogicalDrives()
        if not drive_mask:
            raise DriveError("Unable to enumerate assigned drive letters")
        return [letter for index, letter in enumerate(string.ascii_uppercase)
                if letter not in "AB" and not drive_mask & (1 << index)]
    return [c for c in string.ascii_uppercase if c not in "AB" and not os.path.exists(f"{c}:\\")]


def mount_drive(folder: os.PathLike | str, letter: str, replace: bool = False) -> str:
    """Map `folder` to `letter`. With replace=True an existing subst on that letter is removed first.

    Returns the drive root, e.g. 'F:\\'.
    """
    letter = _normalize(letter)
    target = Path(folder)
    if not target.is_dir():
        raise DriveError(f"Folder does not exist: {target}")
    existing = list_substituted_drives()
    if letter in existing:
        if not replace:
            raise DriveError(f"Drive {letter}: is already mapped to {existing[letter]}")
        unmount_drive(letter)
    elif os.path.exists(f"{letter}:\\"):
        raise DriveError(f"Drive {letter}: is already in use by a real drive")
    result = _subst(f"{letter}:", str(target.resolve()))
    if result.returncode != 0:
        raise DriveError(f"subst failed: {(result.stdout + result.stderr).strip()}")
    return f"{letter}:\\"


def unmount_drive(
    letter: str, delete_contents: bool = False, expected_target: str | None = None,
) -> None:
    """Remove a subst mapping, optionally deleting its target contents first."""
    letter = _normalize(letter)
    mappings = list_substituted_drives()
    if letter not in mappings:
        raise DriveError(f"Drive {letter}: is not a subst drive")
    target = mappings[letter]
    if expected_target is not None and target != expected_target:
        raise DriveError(f"Drive {letter}: mapping changed; please try again")
    if delete_contents:
        folder = Path(target).resolve()
        if folder == Path(folder.anchor):
            raise DriveError("Refusing to delete the contents of a filesystem root")
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        shutil.rmtree(entry.path)
                    else:
                        os.unlink(entry.path)
        except OSError as error:
            raise DriveError(
                f"Could not delete all contents of {target}; drive was not unmapped. "
                f"Some contents may already have been deleted.\n{error}"
            ) from error
    result = _subst(f"{letter}:", "/D")
    if result.returncode != 0:
        raise DriveError(f"subst /D failed: {(result.stdout + result.stderr).strip()}")
