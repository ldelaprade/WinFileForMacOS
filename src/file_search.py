from __future__ import annotations

import fnmatch
import os
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)


class _SearchSignals(QObject):
    results = Signal(list)
    completed = Signal(list, bool)


class _SearchWorker(QRunnable):
    def __init__(self, root: str, pattern: str) -> None:
        super().__init__()
        self.root = root
        self.pattern = pattern.casefold()
        self.cancelled = threading.Event()
        self.signals = _SearchSignals()

    def run(self) -> None:
        errors: list[str] = []
        batch: list[str] = []

        def on_error(error: OSError) -> None:
            errors.append(str(error))

        for directory, _folders, files in os.walk(self.root, onerror=on_error):
            if self.cancelled.is_set():
                break
            for name in files:
                if self.cancelled.is_set():
                    break
                if fnmatch.fnmatchcase(name.casefold(), self.pattern):
                    batch.append(os.path.join(directory, name))
                    if len(batch) >= 100:
                        self.signals.results.emit(batch)
                        batch = []
        if batch and not self.cancelled.is_set():
            self.signals.results.emit(batch)
        self.signals.completed.emit(errors, self.cancelled.is_set())


class FileSearchDialog(QDialog):
    def __init__(
        self, root: str, parent: QWidget, open_file: Callable[[str], None],
        open_folder: Callable[[str], None],
    ) -> None:
        super().__init__(parent)
        self.root = root
        self.open_file = open_file
        self.open_folder = open_folder
        self.worker: _SearchWorker | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Search Files")
        self.resize(700, 450)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Search in: {root}", self))
        row = QHBoxLayout()
        self.pattern_edit = QLineEdit(self)
        self.pattern_edit.setPlaceholderText("File name or wildcard, e.g. *.avi")
        row.addWidget(self.pattern_edit)
        self.search_button = QPushButton("Search", self)
        self.search_button.clicked.connect(self.start_search)
        row.addWidget(self.search_button)
        self.cancel_button = QPushButton("Stop", self)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.stop_search)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        self.results = QListWidget(self)
        self.results.itemDoubleClicked.connect(self._open_result)
        layout.addWidget(self.results)
        self.status = QLabel("Enter a file name or wildcard.", self)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        self.folder_button = buttons.addButton(
            "Open Containing Folder", QDialogButtonBox.ButtonRole.ActionRole
        )
        self.folder_button.clicked.connect(self._open_containing_folder)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.pattern_edit.returnPressed.connect(self.start_search)
        self.finished.connect(self.stop_search)

    def start_search(self) -> None:
        if self.worker is not None:
            return
        pattern = self.pattern_edit.text().strip()
        if not pattern:
            QMessageBox.warning(self, "Search Files", "Enter a file name or wildcard.")
            return
        if not any(char in pattern for char in "*?["):
            pattern = f"*{pattern}*"
        self.results.clear()
        self.status.setText("Searching...")
        self.search_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.worker = _SearchWorker(self.root, pattern)
        self.worker.signals.results.connect(self._add_results)
        self.worker.signals.completed.connect(self._search_completed)
        QThreadPool.globalInstance().start(self.worker)

    def stop_search(self, *_args: object) -> None:
        if self.worker is not None:
            self.worker.cancelled.set()
            self.status.setText("Stopping...")
            self.cancel_button.setEnabled(False)

    def _add_results(self, paths: list[str]) -> None:
        for path in paths:
            item = QListWidgetItem(path, self.results)
            item.setData(Qt.ItemDataRole.UserRole, path)
        self.status.setText(f"Searching... {self.results.count()} file(s) found")

    def _search_completed(self, errors: list[str], cancelled: bool) -> None:
        self.worker = None
        self.search_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        state = "Stopped" if cancelled else "Search complete"
        self.status.setText(f"{state}: {self.results.count()} file(s) found")
        if errors and self.isVisible():
            QMessageBox.warning(
                self, "Search Files",
                "Some folders could not be searched:\n" + "\n".join(errors),
            )

    def _open_result(self, item: QListWidgetItem) -> None:
        self.open_file(item.data(Qt.ItemDataRole.UserRole))

    def _open_containing_folder(self) -> None:
        item = self.results.currentItem()
        if item is not None:
            self.open_folder(os.path.dirname(item.data(Qt.ItemDataRole.UserRole)))
            self.accept()
