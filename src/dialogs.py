from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QMainWindow, QVBoxLayout


def selection_preview(paths: list[str], max_items: int = 3) -> str:
    names = [Path(path).name or path for path in paths]
    if not names:
        return "(no items)"

    if len(names) <= max_items:
        return "\n".join(f"- {name}" for name in names)

    shown = "\n".join(f"- {name}" for name in names[:max_items])
    remaining = len(names) - max_items
    return f"{shown}\n- ... and {remaining} more"


def build_move_confirmation_message(destination_path: str, source_paths: list[str]) -> str:
    item_count = len(source_paths)
    if item_count == 0:
        return "Move selected item(s) to this folder?"

    destination_name = Path(destination_path).name if destination_path else "target folder"
    preview = selection_preview(source_paths)
    if item_count == 1:
        return (
            "Move this item?\n\n"
            f"{preview}\n\n"
            f"Destination: {destination_name}"
        )

    return (
        f"Move {item_count} items?\n\n"
        f"{preview}\n\n"
        f"Destination: {destination_name}"
    )


def build_delete_confirmation_message(paths: list[str]) -> str:
    item_count = len(paths)
    preview = selection_preview(paths)
    if item_count == 1:
        return f"Delete this item permanently?\n\n{preview}"
    return f"Delete {item_count} items permanently?\n\n{preview}"


class DeleteConfirmDialog(QDialog):
    def __init__(self, message: str, parent: QMainWindow | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Delete")
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint)
        self.setModal(True)

        layout = QVBoxLayout(self)
        text_label = QLabel(message, self)
        text_label.setWordWrap(True)
        layout.addWidget(text_label)

        self.button_box = QDialogButtonBox(self)
        self.yes_button = self.button_box.addButton("Yes", QDialogButtonBox.AcceptRole)
        self.no_button = self.button_box.addButton("No", QDialogButtonBox.RejectRole)

        self.no_button.setDefault(True)
        self.no_button.setAutoDefault(True)
        self.no_button.setFocus()
        self.yes_button.setAutoDefault(True)

        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            focused = self.focusWidget()
            if focused is self.yes_button:
                self.accept()
                return
            if focused is self.no_button:
                self.reject()
                return
        super().keyPressEvent(event)


class ActionConfirmDialog(QDialog):
    def __init__(
        self,
        title: str,
        message: str,
        yes_label: str = "Yes",
        no_label: str = "No",
        parent: QMainWindow | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(message, self))

        self.button_box = QDialogButtonBox(self)
        self.yes_button = self.button_box.addButton(yes_label, QDialogButtonBox.AcceptRole)
        self.no_button = self.button_box.addButton(no_label, QDialogButtonBox.RejectRole)

        self.no_button.setDefault(True)
        self.no_button.setAutoDefault(True)
        self.no_button.setFocus()
        self.yes_button.setAutoDefault(True)

        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            focused = self.focusWidget()
            if focused is self.yes_button:
                self.accept()
                return
            if focused is self.no_button:
                self.reject()
                return
        super().keyPressEvent(event)
