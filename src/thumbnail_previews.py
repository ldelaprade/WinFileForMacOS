from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QFileIconProvider


class ThumbnailPreviewProvider:
    _PREVIEWABLE_IMAGE_EXTENSIONS = {
        ".bmp",
        ".gif",
        ".heic",
        ".jpeg",
        ".jpg",
        ".png",
        ".tif",
        ".tiff",
        ".webp",
    }
    _PREVIEWABLE_DOCUMENT_EXTENSIONS = {
        ".pdf",
    }
    _PREVIEWABLE_VIDEO_EXTENSIONS = {
        ".avi",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".webm",
    }

    def __init__(self, icon_provider: QFileIconProvider, max_cache_size: int = 500) -> None:
        self.icon_provider = icon_provider
        self.max_cache_size = max_cache_size
        self._thumbnail_icon_cache: dict[tuple[str, int, int, int, int], QIcon] = {}

    def icon_for_path(self, path: str, icon_size: QSize) -> QIcon:
        if os.path.isdir(path):
            return self.icon_provider.icon(QFileIconProvider.Folder)

        cache_key = self._thumbnail_cache_key(path, icon_size)
        if cache_key is not None:
            cached_icon = self._thumbnail_icon_cache.get(cache_key)
            if cached_icon is not None:
                return cached_icon

        suffix = Path(path).suffix.lower()
        preview = QPixmap()
        if suffix in self._PREVIEWABLE_IMAGE_EXTENSIONS:
            preview = QPixmap(path)
        elif (
            suffix in self._PREVIEWABLE_DOCUMENT_EXTENSIONS
            or suffix in self._PREVIEWABLE_VIDEO_EXTENSIONS
        ):
            preview = self._quicklook_preview_pixmap(path, icon_size)

        if not preview.isNull():
            icon = self._icon_from_preview_pixmap(preview, icon_size)
        else:
            icon = self.icon_provider.icon(QFileIconProvider.File)

        if cache_key is not None:
            if len(self._thumbnail_icon_cache) > self.max_cache_size:
                self._thumbnail_icon_cache.clear()
            self._thumbnail_icon_cache[cache_key] = icon

        return icon

    @staticmethod
    def _thumbnail_cache_key(path: str, icon_size: QSize) -> tuple[str, int, int, int, int] | None:
        try:
            stat = os.stat(path)
            return (
                path,
                stat.st_mtime_ns,
                stat.st_size,
                icon_size.width(),
                icon_size.height(),
            )
        except OSError:
            return None

    @staticmethod
    def _icon_from_preview_pixmap(preview: QPixmap, icon_size: QSize) -> QIcon:
        scaled = preview.scaled(
            icon_size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        canvas = QPixmap(icon_size)
        canvas.fill(Qt.transparent)

        painter = QPainter(canvas)
        x = (icon_size.width() - scaled.width()) // 2
        y = (icon_size.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        painter.end()
        return QIcon(canvas)

    @staticmethod
    def _quicklook_preview_pixmap(path: str, icon_size: QSize) -> QPixmap:
        preview_size = str(max(icon_size.width(), icon_size.height(), 256))
        try:
            with tempfile.TemporaryDirectory(prefix="winfile-preview-") as temp_dir:
                subprocess.run(
                    ["qlmanage", "-t", "-s", preview_size, "-o", temp_dir, path],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

                preview_files = sorted(
                    Path(temp_dir).glob("*.png"),
                    key=lambda candidate: candidate.stat().st_mtime_ns,
                    reverse=True,
                )

                for preview_file in preview_files:
                    pixmap = QPixmap(str(preview_file))
                    if not pixmap.isNull():
                        return pixmap
        except OSError:
            pass

        return QPixmap()
