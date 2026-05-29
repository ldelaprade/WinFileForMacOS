from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QFileSystemModel, QTreeView


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
