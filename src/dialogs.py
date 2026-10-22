from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import SplitResult, unquote, urlsplit

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QPushButton,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from .git_operations import GitBranchInfo, GitChanges
    from .svn_operations import SvnChanges


class GitStagingDialog(QDialog):
    def __init__(self, changes: GitChanges, action: str, parent: QMainWindow | None = None) -> None:
        super().__init__(parent)
        self.action = action
        title = "Staging" if action == "stage" else "Unstaging"
        if changes.branch:
            title = f"{title} - {changes.branch}"
        self.setWindowTitle(title)
        self.resize(760, 480)
        layout = QVBoxLayout(self)
        repository_label = QLabel(changes.root, self)
        repository_label.setWordWrap(True)
        layout.addWidget(repository_label)
        self.file_lists: list[QListWidget] = []
        self.check_all_buttons: list[QPushButton] = []
        self.uncheck_all_buttons: list[QPushButton] = []
        sections = (
            [("Modified Files", changes.modified), ("Untracked Files", changes.untracked)]
            if action == "stage" else [("Staged Files", changes.staged)]
        )
        for title, entries in sections:
            file_list = QListWidget(self)
            file_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
            self.file_lists.append(file_list)
            section_header = QHBoxLayout()
            section_title = f"{title} ({len(entries)})"
            if action == "unstage":
                section_title += ", please check files you want to unstage"
            section_header.addWidget(QLabel(section_title, self))
            section_header.addStretch()
            check_all_button = QPushButton("Check All", self)
            check_all_button.clicked.connect(
                lambda checked=False, target=file_list: self._check_all(target)
            )
            self.check_all_buttons.append(check_all_button)
            section_header.addWidget(check_all_button)
            uncheck_all_button = QPushButton("Uncheck All", self)
            uncheck_all_button.clicked.connect(
                lambda checked=False, target=file_list: self._uncheck_all(target)
            )
            self.uncheck_all_buttons.append(uncheck_all_button)
            section_header.addWidget(uncheck_all_button)
            layout.addLayout(section_header)
            for change in entries:
                label = f"{change.original_path} -> {change.path}" if change.original_path else change.path
                item = QListWidgetItem(label, file_list)
                paths = [str(Path(changes.root) / change.path)]
                if change.original_path:
                    paths.append(str(Path(changes.root) / change.original_path))
                item.setData(Qt.ItemDataRole.UserRole, paths)
                item.setToolTip(label)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
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
        paths: list[str] = []
        for file_list in self.file_lists:
            for row in range(file_list.count()):
                item = file_list.item(row)
                if item.checkState() == Qt.CheckState.Checked:
                    paths.extend(item.data(Qt.ItemDataRole.UserRole))
        return list(dict.fromkeys(paths))

    def _update_action_button(self) -> None:
        self.action_button.setEnabled(bool(self.selected_paths()))
        for check_button, uncheck_button, file_list in zip(
            self.check_all_buttons, self.uncheck_all_buttons, self.file_lists
        ):
            states = [file_list.item(row).checkState() for row in range(file_list.count())]
            check_button.setEnabled(any(state != Qt.CheckState.Checked for state in states))
            uncheck_button.setEnabled(any(state == Qt.CheckState.Checked for state in states))

    @staticmethod
    def _check_all(file_list: QListWidget) -> None:
        for row in range(file_list.count()):
            file_list.item(row).setCheckState(Qt.CheckState.Checked)

    @staticmethod
    def _uncheck_all(file_list: QListWidget) -> None:
        for row in range(file_list.count()):
            file_list.item(row).setCheckState(Qt.CheckState.Unchecked)


class GitRepositoryStatusDialog(QDialog):
    def __init__(
        self,
        status: str,
        branches: GitBranchInfo,
        operation_callback: Callable[[str, list[str]], None],
        parent: QMainWindow | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Repository Status")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(760, 620)
        self.operation_callback = operation_callback
        self.current_branch = branches.current
        layout = QVBoxLayout(self)
        current_branch = branches.current if branches.current is not None else "Detached HEAD"
        layout.addWidget(QLabel(f"Current branch: {current_branch}", self))

        self.publish_group = QGroupBox("Publish current branch", self)
        publish_layout = QHBoxLayout(self.publish_group)
        self.publish_remote_combo = QComboBox(self.publish_group)
        self.publish_remote_combo.addItems(branches.remotes)
        if self.publish_remote_combo.findText("origin") >= 0:
            self.publish_remote_combo.setCurrentText("origin")
        if not branches.remotes:
            self.publish_remote_combo.addItem("No remotes configured")
            self.publish_remote_combo.setEnabled(False)
        self.publish_button = QPushButton("Publish Branch", self.publish_group)
        self.publish_button.clicked.connect(self._publish_current_branch)
        publish_layout.addWidget(QLabel("Remote:", self.publish_group))
        publish_layout.addWidget(self.publish_remote_combo, 1)
        publish_layout.addWidget(self.publish_button)
        self.publish_group.setVisible(branches.current is not None and branches.upstream is None)
        self.publish_button.setEnabled(bool(branches.current and branches.remotes))
        layout.addWidget(self.publish_group)

        local_group = QGroupBox("Local branches", self)
        local_layout = QHBoxLayout(local_group)
        self.local_branch_combo = QComboBox(local_group)
        self.local_branch_combo.addItems(branches.local)
        if branches.current is not None:
            index = self.local_branch_combo.findText(branches.current)
            if index >= 0:
                self.local_branch_combo.setCurrentIndex(index)
        self.switch_button = QPushButton("Switch", local_group)
        self.switch_button.clicked.connect(self._switch_branch)
        self.local_branch_combo.currentTextChanged.connect(self._update_switch_button)
        local_layout.addWidget(self.local_branch_combo, 1)
        local_layout.addWidget(self.switch_button)
        layout.addWidget(local_group)

        remote_group = QGroupBox("Remote branches", self)
        remote_layout = QFormLayout(remote_group)
        self.remote_branch_combo = QComboBox(remote_group)
        self.local_tracking_name = QLineEdit(remote_group)
        for remote_ref, local_name in branches.remote:
            self.remote_branch_combo.addItem(remote_ref, (remote_ref, local_name))
        if branches.remote:
            self.local_tracking_name.setText(branches.remote[0][1])
        self.remote_branch_combo.currentIndexChanged.connect(self._update_tracking_name)
        self.track_button = QPushButton("Create Tracking Branch", remote_group)
        self.track_button.clicked.connect(self._track_remote_branch)
        self.remote_branch_combo.currentIndexChanged.connect(self._update_track_button)
        self.local_tracking_name.textChanged.connect(self._update_track_button)
        remote_layout.addRow("Remote branch:", self.remote_branch_combo)
        remote_layout.addRow("Local branch name:", self.local_tracking_name)
        remote_layout.addRow("", self.track_button)
        layout.addWidget(remote_group)

        create_group = QGroupBox("Create an unlinked local branch", self)
        create_layout = QHBoxLayout(create_group)
        self.new_branch_name = QLineEdit(create_group)
        self.new_branch_name.setPlaceholderText("Branch name")
        self.create_branch_button = QPushButton("Create and Switch", create_group)
        self.create_branch_button.clicked.connect(self._create_local_branch)
        self.new_branch_name.textChanged.connect(
            lambda name: self.create_branch_button.setEnabled(bool(name.strip()))
        )
        self.create_branch_button.setEnabled(False)
        create_layout.addWidget(self.new_branch_name, 1)
        create_layout.addWidget(self.create_branch_button)
        layout.addWidget(create_group)

        self.status_text = QPlainTextEdit(self)
        self.status_text.setReadOnly(True)
        self.status_text.setPlainText(status)
        layout.addWidget(self.status_text, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        for label, action in (("Fetch", "fetch"), ("Push", "push"), ("Pull", "pull")):
            button = buttons.addButton(label, QDialogButtonBox.ButtonRole.ActionRole)
            button.clicked.connect(lambda checked=False, operation=action: self._run_operation(operation, []))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_switch_button()
        self._update_track_button()

    def _update_switch_button(self) -> None:
        self.switch_button.setEnabled(
            bool(self.local_branch_combo.currentText())
            and self.local_branch_combo.currentText() != self.current_branch
        )

    def _update_tracking_name(self, index: int) -> None:
        branch = self.remote_branch_combo.itemData(index)
        if branch is not None:
            self.local_tracking_name.setText(branch[1])
        self._update_track_button()

    def _update_track_button(self) -> None:
        self.track_button.setEnabled(
            self.remote_branch_combo.currentIndex() >= 0 and bool(self.local_tracking_name.text().strip())
        )

    def _switch_branch(self) -> None:
        self._run_operation("switch", [self.local_branch_combo.currentText()])

    def _track_remote_branch(self) -> None:
        branch = self.remote_branch_combo.currentData()
        if branch is not None:
            self._run_operation("track", [branch[0], self.local_tracking_name.text().strip()])

    def _create_local_branch(self) -> None:
        self._run_operation("create_branch", [self.new_branch_name.text().strip()])

    def _publish_current_branch(self) -> None:
        remote = self.publish_remote_combo.currentText()
        if remote and self.current_branch:
            self._run_operation("publish", [remote, self.current_branch])

    def _run_operation(self, action: str, paths: list[str]) -> None:
        self.accept()
        self.operation_callback(action, paths)


class SvnSelectionDialog(QDialog):
    def __init__(self, changes: SvnChanges, action: str, parent: QMainWindow | None = None) -> None:
        super().__init__(parent)
        titles = {
            "add": (
                "Add Selected",
                "Unversioned Items",
                "Select files and folders that are not yet tracked by SVN.",
                "No unversioned items found.",
            ),
            "delete": (
                "Schedule Deletion",
                "Changed Versioned Items",
                "Only changed or missing tracked items are listed. To delete a clean tracked file, remove it from disk first; it will then appear here as Missing. Selecting an item schedules its deletion in this working copy. Commit afterward to remove it from the repository.",
                "No changed or missing tracked items. To delete a clean tracked file, remove it from disk first; it will then appear here.",
            ),
            "commit": (
                "Commit Selected",
                "Committable Versioned Changes",
                "Select tracked changes, including scheduled deletions, to send to the repository. Unversioned items must be added first.",
                "No committable versioned changes found.",
            ),
        }
        if action not in titles:
            raise ValueError(f"Unsupported SVN selection action: {action}")
        self.action = action
        action_title, list_title, description, empty_state = titles[action]
        self.setWindowTitle(f"SVN {action_title}")
        self.resize(720, 480)
        layout = QVBoxLayout(self)
        working_copy_label = QLabel(changes.directory, self)
        working_copy_label.setWordWrap(True)
        layout.addWidget(working_copy_label)
        self.description_label = QLabel(description, self)
        self.description_label.setWordWrap(True)
        layout.addWidget(self.description_label)
        self.file_list = QListWidget(self)
        self.file_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        for change in changes.for_action(action):
            try:
                display_path = Path(change.path).relative_to(changes.directory).as_posix()
            except ValueError:
                display_path = change.path
            item = QListWidgetItem(f"{change.status.title()} - {display_path}", self.file_list)
            item.setData(Qt.ItemDataRole.UserRole, change.path)
            item.setToolTip(change.path)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
        self.empty_state_label = QLabel(empty_state, self)
        self.empty_state_label.setWordWrap(True)
        self.empty_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state_label.setVisible(self.file_list.count() == 0)
        self.file_list.setVisible(self.file_list.count() > 0)
        selection_header = QHBoxLayout()
        selection_header.addWidget(QLabel(f"{list_title} ({self.file_list.count()})", self))
        selection_header.addStretch()
        self.check_all_button = QPushButton("Check All", self)
        self.check_all_button.clicked.connect(self._check_all)
        self.uncheck_all_button = QPushButton("Uncheck All", self)
        self.uncheck_all_button.clicked.connect(self._uncheck_all)
        selection_header.addWidget(self.check_all_button)
        selection_header.addWidget(self.uncheck_all_button)
        layout.addLayout(selection_header)
        layout.addWidget(self.empty_state_label)
        layout.addWidget(self.file_list)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        self.action_button = buttons.addButton(action_title, QDialogButtonBox.ButtonRole.AcceptRole)
        self.action_button.setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.file_list.itemChanged.connect(self._update_buttons)
        self._update_buttons()

    def selected_paths(self) -> list[str]:
        return [
            self.file_list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.file_list.count())
            if self.file_list.item(row).checkState() == Qt.CheckState.Checked
        ]

    def _update_buttons(self) -> None:
        states = [
            self.file_list.item(row).checkState()
            for row in range(self.file_list.count())
        ]
        self.action_button.setEnabled(any(state == Qt.CheckState.Checked for state in states))
        self.check_all_button.setEnabled(any(state != Qt.CheckState.Checked for state in states))
        self.uncheck_all_button.setEnabled(any(state == Qt.CheckState.Checked for state in states))

    def _check_all(self) -> None:
        for row in range(self.file_list.count()):
            self.file_list.item(row).setCheckState(Qt.CheckState.Checked)

    def _uncheck_all(self) -> None:
        for row in range(self.file_list.count()):
            self.file_list.item(row).setCheckState(Qt.CheckState.Unchecked)


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


def build_trash_confirmation_message(paths: list[str]) -> str:
    item_count = len(paths)
    preview = selection_preview(paths)
    if item_count == 1:
        return f"Move this item to the system Trash?\n\n{preview}"
    return f"Move {item_count} items to the system Trash?\n\n{preview}"


class DeleteConfirmDialog(QDialog):
    def __init__(
        self,
        message: str,
        parent: QMainWindow | None = None,
        *,
        title: str = "Delete",
        accept_label: str = "Yes",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint)
        self.setModal(True)

        layout = QVBoxLayout(self)
        text_label = QLabel(message, self)
        text_label.setWordWrap(True)
        layout.addWidget(text_label)

        self.button_box = QDialogButtonBox(self)
        self.yes_button = self.button_box.addButton(accept_label, QDialogButtonBox.AcceptRole)
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
