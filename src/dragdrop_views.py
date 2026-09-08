from __future__ import annotations

import os

from PySide6.QtCore import QMimeData, Qt, QUrl
from PySide6.QtGui import QDrag, QDropEvent
from PySide6.QtWidgets import QFileSystemModel, QListWidget, QTreeView


class ConfirmingDropTreeView(QTreeView):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._confirm_move_callback = None

    def set_move_confirm_callback(self, callback) -> None:
        self._confirm_move_callback = callback

    def dropEvent(self, event: QDropEvent) -> None:
        is_move_drop = event.proposedAction() == Qt.MoveAction or event.dropAction() == Qt.MoveAction
        destination_path = self._resolve_drop_destination_path(event)
        source_paths = self._resolve_drag_source_paths(event)
        if (
            is_move_drop
            and event.source() is not None
            and self._confirm_move_callback is not None
            and not self._confirm_move_callback(destination_path, source_paths)
        ):
            event.ignore()
            return
        super().dropEvent(event)

    def startDrag(self, supported_actions: Qt.DropAction) -> None:
        source_paths = self._selected_paths_for_drag()
        if not source_paths:
            super().startDrag(supported_actions)
            return

        mime_data = _build_file_drag_mime_data(source_paths)

        drag = QDrag(self)
        drag.setMimeData(mime_data)
        drag.exec(Qt.CopyAction | Qt.MoveAction, Qt.CopyAction)

    def _resolve_drop_destination_path(self, event: QDropEvent) -> str:
        if hasattr(event, "position"):
            drop_pos = event.position().toPoint()
        else:
            drop_pos = event.pos()

        target_index = self.indexAt(drop_pos)
        if not target_index.isValid():
            target_index = self.rootIndex()

        model = self.model()
        if isinstance(model, QFileSystemModel) and target_index.isValid():
            target_path = model.filePath(target_index)
            if target_path and os.path.isfile(target_path):
                return os.path.dirname(target_path)
            return target_path
        return ""

    def _resolve_drag_source_paths(self, event: QDropEvent) -> list[str]:
        urls = event.mimeData().urls()
        local_paths = [url.toLocalFile() for url in urls if url.isLocalFile()]
        if local_paths:
            return local_paths

        source_view = event.source()
        if isinstance(source_view, QTreeView):
            selection_model = source_view.selectionModel()
            model = source_view.model()
            if selection_model is not None and isinstance(model, QFileSystemModel):
                selected_rows = selection_model.selectedRows()
                return [model.filePath(index) for index in selected_rows if index.isValid()]
        return []

    def _selected_paths_for_drag(self) -> list[str]:
        selection_model = self.selectionModel()
        model = self.model()
        if selection_model is None or not isinstance(model, QFileSystemModel):
            return []
        selected_rows = selection_model.selectedRows()
        return [model.filePath(index) for index in selected_rows if index.isValid()]


class FileDragListWidget(QListWidget):
    def startDrag(self, supported_actions: Qt.DropAction) -> None:
        selected_items = self.selectedItems()
        source_paths = [item.data(Qt.UserRole) for item in selected_items if item.data(Qt.UserRole)]
        if not source_paths:
            super().startDrag(supported_actions)
            return

        mime_data = _build_file_drag_mime_data(source_paths)

        drag = QDrag(self)
        drag.setMimeData(mime_data)
        drag.exec(Qt.CopyAction | Qt.MoveAction, Qt.CopyAction)


def _build_file_drag_mime_data(source_paths: list[str]) -> QMimeData:
    mime_data = QMimeData()
    urls = [QUrl.fromLocalFile(path) for path in source_paths]
    mime_data.setUrls(urls)

    # Some browser upload targets require the explicit uri-list payload.
    uri_list = "\r\n".join(url.toString(QUrl.FullyEncoded) for url in urls) + "\r\n"
    mime_data.setData("text/uri-list", uri_list.encode("utf-8"))
    return mime_data
