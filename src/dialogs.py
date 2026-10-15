from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import SplitResult, unquote, urlsplit

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSpinBox,
    QPushButton,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from .git_operations import GitChanges


class GitStagingDialog(QDialog):
    def __init__(self, changes: GitChanges, action: str, parent: QMainWindow | None = None) -> None:
        super().__init__(parent)
        self.action = action
        self.setWindowTitle("Staging" if action == "stage" else "Unstaging")
        self.resize(760, 480)
        layout = QVBoxLayout(self)
        repository_label = QLabel(changes.root, self)
        repository_label.setWordWrap(True)
        layout.addWidget(repository_label)
        self.file_lists: list[QListWidget] = []
        sections = (
            [("Modified Files", changes.modified), ("Untracked Files", changes.untracked)]
            if action == "stage" else [("Staged Files", changes.staged)]
        )
        for title, entries in sections:
            layout.addWidget(QLabel(f"{title} ({len(entries)})", self))
            file_list = QListWidget(self)
            file_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
            self.file_lists.append(file_list)
            for change in entries:
                label = f"{change.original_path} -> {change.path}" if change.original_path else change.path
                item = QListWidgetItem(label, file_list)
                paths = [str(Path(changes.root) / change.path)]
                if change.original_path:
                    paths.append(str(Path(changes.root) / change.original_path))
                item.setData(Qt.ItemDataRole.UserRole, paths)
                item.setToolTip(label)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked if action == "stage" else Qt.CheckState.Checked)
            file_list.itemChanged.connect(self._update_action_button)
            layout.addWidget(file_list)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        self.action_button = buttons.addButton(
            "Stage" if action == "stage" else "Unstage", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.action_button.setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_action_button()

    def selected_paths(self) -> list[str]:
        target_state = Qt.CheckState.Checked if self.action == "stage" else Qt.CheckState.Unchecked
        paths: list[str] = []
        for file_list in self.file_lists:
            for row in range(file_list.count()):
                item = file_list.item(row)
                if item.checkState() == target_state:
                    paths.extend(item.data(Qt.ItemDataRole.UserRole))
        return list(dict.fromkeys(paths))

    def _update_action_button(self) -> None:
        self.action_button.setEnabled(bool(self.selected_paths()))


class SvnCheckoutDialog(QDialog):
    def __init__(
        self,
        parent_directory: str,
        parent: QMainWindow | None = None,
        supported_schemes: set[str] | None = None,
        action_label: str = "Checkout",
        dialog_title: str = "Checkout SVN Working Copy",
        url_placeholder: str = "https://svn.example.com/repository/project",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(dialog_title)
        self.supported_schemes = supported_schemes or {"file", "http", "https", "svn", "svn+ssh"}
        self.setModal(True)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.url_edit = QLineEdit(self)
        self.url_edit.setPlaceholderText(url_placeholder)
        form.addRow("Repository URL:", self.url_edit)

        self.parent_directory_edit = QLineEdit(parent_directory, self)
        browse_button = QPushButton("Browse...", self)
        browse_button.clicked.connect(self._browse_parent_directory)
        parent_row = QHBoxLayout()
        parent_row.addWidget(self.parent_directory_edit)
        parent_row.addWidget(browse_button)
        form.addRow("Local parent folder:", parent_row)

        self.folder_name_edit = QLineEdit(self)
        self.folder_name_edit.setPlaceholderText("Folder name from repository URL")
        form.addRow("Checkout folder:", self.folder_name_edit)
        layout.addLayout(form)

        self.checkout_button = QPushButton(action_label, self)
        self.cancel_button = QPushButton("Cancel", self)
        self.checkout_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.checkout_button)
        layout.addLayout(buttons)

        self._folder_name_edited = False
        self.url_edit.textChanged.connect(self._suggest_folder_name)
        self.url_edit.textChanged.connect(self._update_checkout_button)
        self.folder_name_edit.textEdited.connect(self._mark_folder_name_edited)
        self.parent_directory_edit.textChanged.connect(self._update_checkout_button)
        self.folder_name_edit.textChanged.connect(self._update_checkout_button)
        self._update_checkout_button()

    def values(self) -> tuple[str, str]:
        destination = Path(self.parent_directory_edit.text().strip()).expanduser() / self.folder_name_edit.text().strip()
        return self.url_edit.text().strip(), str(destination.absolute())

    def _browse_parent_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Select Local Parent Folder", self.parent_directory_edit.text()
        )
        if directory:
            self.parent_directory_edit.setText(directory)

    def _suggest_folder_name(self, url: str) -> None:
        if self._folder_name_edited:
            return
        try:
            path_parts = urlsplit(url.strip()).path.rstrip("/").split("/")
            folder_name = unquote(path_parts[-1]) if path_parts and path_parts[-1] else ""
        except ValueError:
            folder_name = ""
        self.folder_name_edit.setText(folder_name)

    def _mark_folder_name_edited(self) -> None:
        self._folder_name_edited = True

    def _update_checkout_button(self) -> None:
        url = self.url_edit.text().strip()
        try:
            parsed_url = urlsplit(url)
        except ValueError:
            self.checkout_button.setEnabled(False)
            return
        parent_directory = Path(self.parent_directory_edit.text().strip()).expanduser()
        folder_name = self.folder_name_edit.text().strip()
        valid_url = self._is_valid_repository_url(url, parsed_url)
        valid_folder = bool(folder_name) and folder_name not in {".", ".."} and not any(
            separator in folder_name for separator in ("/", "\\")
        )
        destination = parent_directory / folder_name
        safe_destination = not destination.exists() or (
            destination.is_dir() and not any(destination.iterdir())
        )
        self.checkout_button.setEnabled(
            valid_url and valid_folder and parent_directory.is_dir() and safe_destination
        )

    def _is_valid_repository_url(self, url: str, parsed_url: SplitResult) -> bool:
        return parsed_url.scheme in self.supported_schemes and bool(
            parsed_url.path.strip("/")
        ) and parsed_url.username is None and parsed_url.password is None


class GitCloneDialog(SvnCheckoutDialog):
    def __init__(self, parent_directory: str, parent: QMainWindow | None = None) -> None:
        super().__init__(
            parent_directory,
            parent,
            supported_schemes={"file", "http", "https", "ssh", "git"},
            action_label="Clone",
            dialog_title="Clone Git Repository",
            url_placeholder="https://github.com/owner/repository.git",
        )
        self.url_edit.textChanged.disconnect(self._suggest_folder_name)
        self.url_edit.textChanged.connect(self._suggest_git_folder_name)

    def _is_valid_repository_url(self, url: str, parsed_url: SplitResult) -> bool:
        if parsed_url.scheme in self.supported_schemes:
            return bool(parsed_url.path.strip("/")) and parsed_url.username is None and parsed_url.password is None
        if parsed_url.scheme:
            return False
        if ":" in url:
            host, path = url.split(":", 1)
            if path and host and "/" not in host and not any(character.isspace() for character in host):
                return True
        return bool(url)

    def _suggest_git_folder_name(self, url: str) -> None:
        if self._folder_name_edited:
            return
        try:
            parsed_url = urlsplit(url.strip())
        except ValueError:
            self.folder_name_edit.clear()
            return
        path = parsed_url.path
        if not path and "@" in url and ":" in url:
            path = url.rsplit(":", 1)[-1]
        folder_name = unquote(path.rstrip("/").rsplit("/", 1)[-1]) if path else ""
        if folder_name.lower().endswith(".git"):
            folder_name = folder_name[:-4]
        self.folder_name_edit.setText(folder_name)


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


class WindowsShareDialog(QDialog):
    def __init__(self, parent: QMainWindow | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Network Location")
        self.setModal(True)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.server_edit = QLineEdit(self)
        self.server_edit.setPlaceholderText("server or IP address")
        form.addRow("Server:", self.server_edit)

        self.share_edit = QLineEdit(self)
        self.share_edit.setPlaceholderText("share name")
        form.addRow("Share:", self.share_edit)

        self.folder_edit = QLineEdit(self)
        self.folder_edit.setPlaceholderText("optional subfolder")
        form.addRow("Folder:", self.folder_edit)

        self.domain_edit = QLineEdit(self)
        self.domain_edit.setPlaceholderText("optional domain")
        form.addRow("Domain:", self.domain_edit)

        self.username_edit = QLineEdit(self)
        form.addRow("User Name:", self.username_edit)

        self.password_edit = QLineEdit(self)
        self.password_edit.setEchoMode(QLineEdit.Password)
        form.addRow("Password:", self.password_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(self)
        buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        self.add_button = buttons.addButton("Add", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.server_edit.setFocus()

    def _accept_if_valid(self) -> None:
        if not self.server_edit.text().strip():
            QMessageBox.warning(self, "Add Network Location", "Enter a server name or IP address.")
            self.server_edit.setFocus()
            return
        if not self.share_edit.text().strip():
            QMessageBox.warning(self, "Add Network Location", "Enter a share name.")
            self.share_edit.setFocus()
            return
        self.accept()

    def values(self) -> dict[str, str]:
        return {
            "server": self.server_edit.text().strip(),
            "share": self.share_edit.text().strip(),
            "folder": self.folder_edit.text().strip(),
            "domain": self.domain_edit.text().strip(),
            "username": self.username_edit.text().strip(),
            "password": self.password_edit.text(),
        }


class SshLocationDialog(QDialog):
    def __init__(self, parent: QMainWindow | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add SSH Network Location")
        self.setModal(True)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.server_edit = QLineEdit(self)
        self.server_edit.setPlaceholderText("server or IP address")
        form.addRow("Server:", self.server_edit)

        self.port_edit = QSpinBox(self)
        self.port_edit.setRange(1, 65535)
        self.port_edit.setValue(22)
        form.addRow("Port:", self.port_edit)

        self.folder_edit = QLineEdit(self)
        self.folder_edit.setPlaceholderText("optional remote folder")
        form.addRow("Folder:", self.folder_edit)

        self.username_edit = QLineEdit(self)
        form.addRow("User Name:", self.username_edit)

        self.password_edit = QLineEdit(self)
        self.password_edit.setEchoMode(QLineEdit.Password)
        form.addRow("Password:", self.password_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(self)
        buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        self.add_button = buttons.addButton("Add", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.server_edit.setFocus()

    def _accept_if_valid(self) -> None:
        if not self.server_edit.text().strip():
            QMessageBox.warning(self, "Add SSH Network Location", "Enter a server name or IP address.")
            self.server_edit.setFocus()
            return
        self.accept()

    def values(self) -> dict[str, str | int]:
        return {
            "server": self.server_edit.text().strip(),
            "port": self.port_edit.value(),
            "folder": self.folder_edit.text().strip(),
            "username": self.username_edit.text().strip(),
            "password": self.password_edit.text(),
        }


class FtpLocationDialog(QDialog):
    def __init__(self, parent: QMainWindow | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add FTP Network Location")
        self.setModal(True)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.server_edit = QLineEdit(self)
        self.server_edit.setPlaceholderText("server or IP address")
        form.addRow("Server:", self.server_edit)

        self.port_edit = QSpinBox(self)
        self.port_edit.setRange(1, 65535)
        self.port_edit.setValue(21)
        form.addRow("Port:", self.port_edit)

        self.folder_edit = QLineEdit(self)
        self.folder_edit.setPlaceholderText("optional remote folder")
        form.addRow("Folder:", self.folder_edit)

        self.username_edit = QLineEdit(self)
        self.username_edit.setPlaceholderText("anonymous if blank")
        form.addRow("User Name:", self.username_edit)

        self.password_edit = QLineEdit(self)
        self.password_edit.setEchoMode(QLineEdit.Password)
        form.addRow("Password:", self.password_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(self)
        buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        self.add_button = buttons.addButton("Add", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.server_edit.setFocus()

    def _accept_if_valid(self) -> None:
        if not self.server_edit.text().strip():
            QMessageBox.warning(self, "Add FTP Network Location", "Enter a server name or IP address.")
            self.server_edit.setFocus()
            return
        self.accept()

    def values(self) -> dict[str, str | int]:
        return {
            "server": self.server_edit.text().strip(),
            "port": self.port_edit.value(),
            "folder": self.folder_edit.text().strip(),
            "username": self.username_edit.text().strip(),
            "password": self.password_edit.text(),
        }


class FtpShareDialog(QDialog):
    def __init__(self, default_share_name: str, parent: QMainWindow | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("FTP Share")
        self.setModal(True)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.share_name_edit = QLineEdit(self)
        self.share_name_edit.setText(default_share_name)
        form.addRow("Share Name:", self.share_name_edit)

        self.port_edit = QSpinBox(self)
        self.port_edit.setRange(1, 65535)
        self.port_edit.setValue(2121)
        form.addRow("Port:", self.port_edit)

        self.username_edit = QLineEdit(self)
        self.username_edit.setPlaceholderText("anonymous if blank")
        form.addRow("User Name:", self.username_edit)

        self.password_edit = QLineEdit(self)
        self.password_edit.setEchoMode(QLineEdit.Password)
        form.addRow("Password:", self.password_edit)

        self.read_only_check = QCheckBox("Read-only (recommended)", self)
        self.read_only_check.setChecked(True)
        form.addRow("", self.read_only_check)

        layout.addLayout(form)

        buttons = QDialogButtonBox(self)
        buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        self.share_button = buttons.addButton("Share", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.share_name_edit.setFocus()
        self.share_name_edit.selectAll()

    def _accept_if_valid(self) -> None:
        if not self.share_name_edit.text().strip():
            QMessageBox.warning(self, "FTP Share", "Enter a share name.")
            self.share_name_edit.setFocus()
            return
        if self.username_edit.text().strip() and not self.password_edit.text():
            QMessageBox.warning(
                self,
                "FTP Share",
                "Enter a password for the user name, or clear the user name for anonymous access.",
            )
            self.password_edit.setFocus()
            return
        self.accept()

    def values(self) -> dict[str, str | int | bool]:
        return {
            "share_name": self.share_name_edit.text().strip(),
            "port": self.port_edit.value(),
            "username": self.username_edit.text().strip(),
            "password": self.password_edit.text(),
            "read_only": self.read_only_check.isChecked(),
        }
