from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PasteResult:
    failures: list[str]
    moved_count: int
    copied_count: int
    clipboard_paths: list[str]
    clipboard_mode: str | None


def paste_items(
    clipboard_paths: list[str],
    clipboard_mode: str | None,
    destination_dir: Path,
) -> PasteResult:
    failures: list[str] = []
    moved_count = 0
    copied_count = 0

    if not clipboard_paths or clipboard_mode is None or not destination_dir.is_dir():
        return PasteResult(
            failures=failures,
            moved_count=moved_count,
            copied_count=copied_count,
            clipboard_paths=clipboard_paths,
            clipboard_mode=clipboard_mode,
        )

    for source_path_str in list(clipboard_paths):
        source_path = Path(source_path_str)
        if not source_path.exists():
            failures.append(f"{source_path}: not found")
            continue

        target_path = destination_dir / source_path.name
        if source_path.parent == destination_dir:
            continue

        if target_path.exists():
            failures.append(f"{target_path}: destination already exists")
            continue

        try:
            if clipboard_mode == "copy":
                if source_path.is_dir():
                    shutil.copytree(source_path, target_path)
                else:
                    shutil.copy2(source_path, target_path)
                copied_count += 1
            elif clipboard_mode == "cut":
                shutil.move(str(source_path), str(target_path))
                moved_count += 1
        except OSError as error:
            failures.append(f"{source_path} -> {target_path}: {error}")

    remaining_paths = clipboard_paths
    remaining_mode = clipboard_mode
    if clipboard_mode == "cut" and moved_count > 0:
        remaining_paths = [p for p in clipboard_paths if Path(p).exists()]
        if not remaining_paths:
            remaining_mode = None

    return PasteResult(
        failures=failures,
        moved_count=moved_count,
        copied_count=copied_count,
        clipboard_paths=remaining_paths,
        clipboard_mode=remaining_mode,
    )


def rename_item(source: Path, new_name: str) -> None:
    destination = source.with_name(new_name)
    if destination.exists():
        raise FileExistsError(str(destination))
    source.rename(destination)


def delete_items(paths: list[str]) -> list[str]:
    failures: list[str] = []
    for path in paths:
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError as error:
            failures.append(f"{path}: {error}")
    return failures


def create_folder(parent: Path, name: str) -> None:
    destination = parent / name
    destination.mkdir(parents=False, exist_ok=False)
