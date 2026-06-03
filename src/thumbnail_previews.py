from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import QFileInfo, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap
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

    def __init__(
        self,
        icon_provider: QFileIconProvider,
        max_cache_size: int = 500,
        quicklook_min_preview_size: int = 128,
    ) -> None:
        self.icon_provider = icon_provider
        self.native_icon_provider = QFileIconProvider()
        self.max_cache_size = max_cache_size
        self.quicklook_min_preview_size = max(64, quicklook_min_preview_size)
        self._thumbnail_icon_cache: dict[tuple[str, int, int, int, int], QIcon] = {}

    def icon_for_path(self, path: str, icon_size: QSize) -> QIcon:
        cache_key = self._thumbnail_cache_key(path, icon_size)
        if cache_key is not None:
            cached_icon = self._thumbnail_icon_cache.get(cache_key)
            if cached_icon is not None:
                return cached_icon

        if os.path.isdir(path):
            icon = self._thumbnail_folder_icon(path, icon_size)
            if cache_key is not None:
                if len(self._thumbnail_icon_cache) > self.max_cache_size:
                    self._thumbnail_icon_cache.clear()
                self._thumbnail_icon_cache[cache_key] = icon
            return icon

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
            icon = self._thumbnail_fallback_icon(path, icon_size)

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
        content = ThumbnailPreviewProvider._trim_transparent_margins(preview)
        scaled = content.scaled(
            icon_size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        canvas = QPixmap(icon_size)
        canvas.fill(Qt.transparent)

        painter = QPainter(canvas)
        # Use logical pixel dimensions for centering — scaled.width() returns
        # physical pixels, which is 2× on Retina displays. Dividing by
        # devicePixelRatio() gives the logical (device-independent) size that
        # QPainter expects for positioning.
        dpr = scaled.devicePixelRatio()
        x = (icon_size.width() - int(scaled.width() / dpr)) // 2
        y = (icon_size.height() - int(scaled.height() / dpr)) // 2
        painter.drawPixmap(x, y, scaled)
        painter.end()
        return QIcon(canvas)

    @staticmethod
    def _trim_transparent_margins(pixmap: QPixmap) -> QPixmap:
        image = pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
        width = image.width()
        height = image.height()

        min_x = width
        min_y = height
        max_x = -1
        max_y = -1

        for y in range(height):
            for x in range(width):
                if QColor.fromRgba(image.pixel(x, y)).alpha() > 0:
                    min_x = min(min_x, x)
                    min_y = min(min_y, y)
                    max_x = max(max_x, x)
                    max_y = max(max_y, y)

        if max_x < min_x or max_y < min_y:
            return pixmap

        return pixmap.copy(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)

    def _quicklook_preview_pixmap(self, path: str, icon_size: QSize) -> QPixmap:
        preview_size = str(
            max(icon_size.width(), icon_size.height(), self.quicklook_min_preview_size)
        )
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

    def _thumbnail_fallback_icon(self, path: str, icon_size: QSize) -> QIcon:
        native_icon = self.native_icon_provider.icon(QFileInfo(path))
        native_pixmap = native_icon.pixmap(icon_size)
        if not native_pixmap.isNull():
            return self._icon_from_preview_pixmap(native_pixmap, icon_size)

        suffix = Path(path).suffix.upper().lstrip(".")
        label = (suffix[:4] if suffix else "FILE")
        return self._draw_generic_file_icon(icon_size, label)

    def _thumbnail_folder_icon(self, path: str, icon_size: QSize) -> QIcon:
        return self._draw_generic_folder_icon(icon_size)

    @staticmethod
    def _draw_generic_folder_icon(icon_size: QSize) -> QIcon:
        canvas = QPixmap(icon_size)
        canvas.fill(Qt.transparent)

        w = icon_size.width()
        h = icon_size.height()
        body_w = max(88, int(w * 0.82))
        body_h = max(62, int(h * 0.50))
        tab_w = max(36, int(body_w * 0.40))
        tab_h = max(16, int(body_h * 0.30))
        x = (w - body_w) // 2
        y = (h - body_h) // 2 + max(4, int(h * 0.03))
        radius = max(4, int(min(body_w, body_h) * 0.06))

        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.Antialiasing, True)

        # Back plate and tab (XP 2001 brighter yellow)
        painter.setPen(QPen(QColor("#8b6a1f"), 2))
        painter.setBrush(QColor("#f0c84f"))
        painter.drawRoundedRect(x, y, body_w, body_h, radius, radius)

        painter.setPen(QPen(QColor("#8b6a1f"), 2))
        painter.setBrush(QColor("#fbe3a0"))
        painter.drawRoundedRect(x + 5, y - tab_h + 2, tab_w, tab_h, radius * 0.7, radius * 0.7)

        # Front face for slight depth effect
        front_h = max(36, int(body_h * 0.64))
        front_y = y + body_h - front_h
        # painter.setPen(QPen(QColor("#a3791e"), 2))
        # painter.setBrush(QColor("#f5d05d"))
        # painter.drawRoundedRect(x + 3, front_y, body_w - 6, front_h, radius, radius)

        # XP-like highlight and seam lines
        painter.setPen(QPen(QColor("#fff0c3"), 2))
        painter.drawLine(x + 8, y + 8, x + body_w - 10, y + 8)
        painter.setPen(QPen(QColor("#be9130"), 1))
        painter.drawLine(x + 8, front_y + 2, x + body_w - 9, front_y + 2)
        painter.drawLine(x + 8, front_y + front_h // 2, x + body_w - 9, front_y + front_h // 2)

        painter.end()
        return QIcon(canvas)

    @staticmethod
    def _draw_generic_file_icon(icon_size: QSize, label: str) -> QIcon:
        canvas = QPixmap(icon_size)
        canvas.fill(Qt.transparent)

        w = icon_size.width()
        h = icon_size.height()
        doc_w = max(64, int(w * 0.66))
        doc_h = max(82, int(h * 0.78))
        x = (w - doc_w) // 2
        y = (h - doc_h) // 2
        fold = max(12, int(doc_w * 0.20))

        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.Antialiasing, True)

        painter.setPen(QPen(QColor("#6b6b6b"), 2))
        painter.setBrush(QColor("#f8f8f8"))
        painter.drawRect(x, y, doc_w, doc_h)

        painter.fillRect(x + doc_w - fold, y + 1, fold - 1, fold - 1, QColor("#e9e9e9"))

        accent_h = max(22, int(doc_h * 0.18))
        painter.fillRect(x + 1, y + 1, doc_w - 2, accent_h, QColor("#3b78d8"))

        text_rect = QRect(x + 8, y + accent_h + 8, doc_w - 16, doc_h - accent_h - 16)
        font = QFont()
        font.setBold(True)
        font.setPointSize(max(8, int(doc_w * 0.085)))
        painter.setFont(font)
        painter.setPen(QColor("#1d1d1d"))
        painter.drawText(text_rect, Qt.AlignCenter | Qt.TextWordWrap, label)

        painter.end()
        return QIcon(canvas)
