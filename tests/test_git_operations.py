import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from git import Repo

from src.git_operations import GitOperationError, GitOperations


class GitOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.operations = GitOperations(str(self.root))
        self.operations.run("init")
        self.repository = Repo(self.root)
        self.addCleanup(self.repository.close)
        with self.repository.config_writer() as config:
            config.set_value("user", "name", "Explorer Test")
            config.set_value("user", "email", "explorer@example.invalid")
            config.set_value("commit", "gpgsign", "false")

    def write_file(self, name, content="new content\n"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return str(path)

    def test_detects_repository_from_nested_folder(self):
        nested = self.root / "nested"
        nested.mkdir()
        self.assertIn("No commits yet", GitOperations(str(nested)).run("status"))

    def test_clone_local_repository_into_explicit_destination(self):
        source_file = self.write_file("source file.txt", "clone content\n")
        self.operations.run("stage", [source_file])
        self.operations.run("commit", message="Initial clone source")
        destination = self.root / "local clones" / "sample repository"
        destination.parent.mkdir()

        output = GitOperations(str(destination.parent)).run(
            "clone", [str(self.root), str(destination)]
        )

        self.assertIn(str(destination), output)
        self.assertEqual((destination / "source file.txt").read_text(), "clone content\n")
        with Repo(destination) as cloned:
            self.assertEqual(cloned.head.commit.message.strip(), "Initial clone source")

    def test_clone_rejects_empty_url_password_and_nonempty_destination(self):
        destination = self.root / "destination"
        destination.mkdir()
        (destination / "keep.txt").write_text("keep\n")
        for url, target in (
            ("", self.root / "empty-url"),
            ("https://user:token@example.com/repo.git", self.root / "embedded-password"),
            (str(self.root), destination),
        ):
            with self.subTest(url=url), self.assertRaises(GitOperationError):
                self.operations.run("clone", [url, str(target)])
        self.assertEqual((destination / "keep.txt").read_text(), "keep\n")

    def test_timeout_options_are_platform_aware(self):
        for platform in ("win32", "darwin", "linux"):
            for seconds in (30, 120):
                with self.subTest(platform=platform, seconds=seconds), patch(
                    "src.git_operations.sys.platform", platform
                ):
                    expected = {} if platform == "win32" else {"kill_after_timeout": seconds}
                    self.assertEqual(self.operations._timeout_options(seconds), expected)

    def test_changes_separate_modified_untracked_and_staged_repository_wide(self):
        modified = self.write_file("modified.txt")
        deleted = self.write_file("deleted.txt")
        self.operations.run("stage", [modified, deleted])
        self.operations.run("commit", message="Initial files")
        Path(modified).write_text("staged change\n")
        self.operations.run("stage", [modified])
        Path(modified).write_text("additional working change\n")
        Path(deleted).unlink()
        self.write_file("new\nfile.txt")
        self.write_file(".gitignore", "ignored.txt\n")
        self.write_file("ignored.txt")
        nested = self.root / "nested"
        nested.mkdir()

        changes = GitOperations(str(nested)).run("staging")

        self.assertEqual(Path(changes.root), self.root)
        self.assertEqual({change.path for change in changes.modified}, {"modified.txt", "deleted.txt"})
        self.assertEqual({change.path for change in changes.untracked}, {".gitignore", "new\nfile.txt"})
        self.assertEqual([change.path for change in changes.staged], ["modified.txt"])

    def test_changes_before_first_commit_and_empty_repository(self):
        self.assertEqual(self.operations.run("staging").staged, [])
        path = self.write_file("new file.txt")
        self.operations.run("stage", [path])
        changes = self.operations.run("unstaging")
        self.assertEqual([change.path for change in changes.staged], ["new file.txt"])
        self.assertEqual(changes.modified, [])
        self.assertEqual(changes.untracked, [])

    def test_staged_rename_includes_both_paths_for_unstaging(self):
        original = self.write_file("old name.txt")
        self.operations.run("stage", [original])
        self.operations.run("commit", message="Initial file")
        renamed = str(self.root / "new name.txt")
        Path(original).rename(renamed)
        self.operations.run("stage", [original, renamed])

        changes = self.operations.run("unstaging")

        self.assertEqual(len(changes.staged), 1)
        self.assertEqual(changes.staged[0].path, "new name.txt")
        self.assertEqual(changes.staged[0].original_path, "old name.txt")
        self.operations.run("unstage", [renamed, original])
        self.assertEqual(self.operations.run("diff"), "No staged changes.")
        self.assertTrue(Path(renamed).exists())

    def test_stage_and_unstage_before_first_commit_preserves_file(self):
        path = self.write_file("new file.txt")
        self.operations.run("stage", [path])
        self.assertIn("new file.txt", self.operations.run("diff"))
        Path(path).write_text("changed after staging\n")
        self.operations.run("unstage", [path])
        self.assertEqual(self.operations.run("diff"), "No staged changes.")
        self.assertEqual(Path(path).read_text(), "changed after staging\n")

    def test_commit_includes_only_staged_changes(self):
        staged = self.write_file("staged.txt")
        self.write_file("untracked.txt")
        self.operations.run("stage", [staged])
        self.operations.run("commit", message="First commit")
        self.assertEqual(self.repository.head.commit.message.strip(), "First commit")
        self.assertEqual(list(self.repository.head.commit.tree), [self.repository.head.commit.tree["staged.txt"]])
        self.assertIn("untracked.txt", self.operations.run("status"))

    def test_unstage_after_commit_preserves_working_changes(self):
        path = self.write_file("tracked.txt")
        self.operations.run("stage", [path])
        self.operations.run("commit", message="First commit")
        Path(path).write_text("modified\n")
        self.operations.run("stage", [path])
        self.operations.run("unstage", [path])
        self.assertEqual(self.operations.run("diff"), "No staged changes.")
        self.assertEqual(Path(path).read_text(), "modified\n")

    def test_filenames_are_literal_not_pathspec_patterns(self):
        literal = self.write_file("[sample].txt")
        self.write_file("s.txt")
        self.operations.run("stage", [literal])
        self.assertEqual(list(self.repository.index.entries), [("[sample].txt", 0)])

    def test_rejects_paths_outside_repository_and_git_metadata(self):
        for path in (str(self.root.parent / "outside.txt"), str(self.root / ".git" / "config")):
            with self.subTest(path=path), self.assertRaises(GitOperationError):
                self.operations.run("stage", [path])

    def test_rejects_empty_selection_and_commit_message(self):
        for action in ("stage", "unstage", "commit"):
            with self.subTest(action=action), self.assertRaises(GitOperationError):
                self.operations.run(action)

    def test_init_refuses_existing_repository_or_nested_repository(self):
        nested = self.root / "nested"
        nested.mkdir()
        with self.assertRaises(GitOperationError):
            GitOperations(str(nested)).run("init")
        self.assertFalse((nested / ".git").exists())

    def test_non_repository_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(GitOperationError, "not inside a Git repository"):
                GitOperations(directory).run("status")

    def test_push_and_pull_use_configured_upstream(self):
        path = self.write_file("tracked.txt")
        self.operations.run("stage", [path])
        self.operations.run("commit", message="Initial commit")
        with tempfile.TemporaryDirectory() as directory:
            with Repo.init(Path(directory) / "remote.git", bare=True) as remote:
                self.repository.create_remote("origin", remote.git_dir)
                branch = self.repository.active_branch.name
                self.repository.git.push("--set-upstream", "origin", branch)
                Path(path).write_text("local change\n")
                self.operations.run("stage", [path])
                self.operations.run("commit", message="Local change")

                self.assertTrue(self.operations.run("push"))
                self.assertEqual(remote.commit(branch), self.repository.head.commit)

                with Repo.clone_from(remote.git_dir, Path(directory) / "other", branch=branch) as other:
                    with other.config_writer() as config:
                        config.set_value("user", "name", "Explorer Test")
                        config.set_value("user", "email", "explorer@example.invalid")
                        config.set_value("commit", "gpgsign", "false")
                    other_path = Path(other.working_tree_dir) / "tracked.txt"
                    other_path.write_text("remote change\n")
                    other.git.add("tracked.txt")
                    other.git.commit("-m", "Remote change")
                    other.git.push()

                    self.assertTrue(self.operations.run("pull"))
                    self.assertEqual(Path(path).read_text(), "remote change\n")
                    self.assertEqual(self.repository.head.commit, other.head.commit)

                    Path(path).write_text("divergent local change\n")
                    self.operations.run("stage", [path])
                    self.operations.run("commit", message="Divergent local change")
                    local_commit = self.repository.head.commit
                    other_path.write_text("divergent remote change\n")
                    other.git.add("tracked.txt")
                    other.git.commit("-m", "Divergent remote change")
                    other.git.push()
                    with self.assertRaises(GitOperationError):
                        self.operations.run("pull")
                    self.assertEqual(self.repository.head.commit, local_commit)
                    with self.assertRaises(GitOperationError):
                        self.operations.run("push")
                    self.assertEqual(remote.commit(branch), other.head.commit)

    def test_remote_operations_without_configuration_report_errors(self):
        path = self.write_file("tracked.txt")
        self.operations.run("stage", [path])
        self.operations.run("commit", message="Initial commit")
        for action in ("push", "pull"):
            with self.subTest(action=action), self.assertRaises(GitOperationError):
                self.operations.run(action)


class GitExplorerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        from src.file_explorer import ExplorerWindow

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.window = ExplorerWindow(initial_path=self.temporary.name)
        self.addCleanup(self.window.close)

    def test_menu_enables_staging_without_selection(self):
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self.window)
        with patch.object(self.window, "selected_paths", return_value=[]):
            self.window._add_git_menu(menu)
        actions = menu.actions()[0].menu().actions()
        self.assertEqual(actions[0].text(), "Clone Repository...")
        self.assertTrue(actions[2].isEnabled())
        self.assertTrue(actions[3].isEnabled())
        self.assertEqual(actions[2].text(), "Staging...")
        self.assertEqual(actions[3].text(), "Unstaging...")
        self.assertTrue(actions[0].isEnabled())

    def test_menu_uses_current_folder_not_selection(self):
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self.window)
        paths = [str(Path(self.temporary.name) / "sample.txt")]
        with patch.object(self.window, "current_path", return_value=self.temporary.name), patch.object(
            self.window, "selected_paths", return_value=paths
        ), patch.object(self.window, "_start_git_operation") as start:
            self.window._add_git_menu(menu)
            menu.actions()[0].menu().actions()[2].trigger()
            start.assert_called_once_with("staging", self.temporary.name, [])
            start.reset_mock()
            menu.actions()[0].menu().actions()[3].trigger()
            start.assert_called_once_with("unstaging", self.temporary.name, [])

    def test_git_clone_dialog_suggests_folder_from_https_and_ssh_urls(self):
        from PySide6.QtTest import QTest
        from src.dialogs import GitCloneDialog

        dialog = GitCloneDialog(self.temporary.name, self.window)
        self.addCleanup(dialog.close)
        for url, folder in (
            ("https://github.com/owner/sample-repository.git", "sample-repository"),
            ("git@github.com:owner/another-repository.git", "another-repository"),
        ):
            with self.subTest(url=url):
                dialog.url_edit.setText(url)
                self.assertEqual(dialog.folder_name_edit.text(), folder)
                self.assertTrue(dialog.checkout_button.isEnabled())
        dialog.folder_name_edit.selectAll()
        QTest.keyClicks(dialog.folder_name_edit, "custom-name")
        dialog.url_edit.setText("https://github.com/owner/changed.git")
        self.assertEqual(dialog.folder_name_edit.text(), "custom-name")

    def test_clone_menu_starts_dialog_and_background_operation(self):
        from PySide6.QtWidgets import QDialog, QMenu
        from src.dialogs import GitCloneDialog

        menu = QMenu(self.window)
        with patch.object(self.window, "current_path", return_value=self.temporary.name):
            self.window._add_git_menu(menu)
        clone_action = menu.actions()[0].menu().actions()[0]
        destination = str(Path(self.temporary.name) / "repo")
        with patch.object(GitCloneDialog, "exec", return_value=QDialog.DialogCode.Accepted), patch.object(
            GitCloneDialog, "values", return_value=("https://example.com/repo.git", destination)
        ), patch.object(self.window, "_start_git_operation") as start:
            clone_action.trigger()
        start.assert_called_once_with(
            "clone", self.temporary.name, ["https://example.com/repo.git", destination]
        )

    def test_successful_clone_opens_destination(self):
        from src.file_explorer import _GitWorker

        destination = str(Path(self.temporary.name) / "cloned-repository")
        self.window._git_worker = _GitWorker(
            self.temporary.name, "clone", ["https://example.com/repo.git", destination], ""
        )
        with patch.object(self.window, "navigate_to") as navigate, patch.object(
            self.window, "_show_git_output"
        ) as output:
            self.window._git_operation_finished("Clone complete", True)
        navigate.assert_called_once_with(destination, record_history=True)
        output.assert_called_once_with("Clone complete")

    def test_staging_dialog_separates_sections_and_stages_only_checked_files(self):
        from PySide6.QtCore import Qt
        from src.dialogs import GitStagingDialog
        from src.git_operations import GitChange, GitChanges

        changes = GitChanges(self.temporary.name, [GitChange("modified.txt")], [GitChange("new.txt")], [])
        dialog = GitStagingDialog(changes, "stage", self.window)
        self.addCleanup(dialog.close)
        self.assertEqual(len(dialog.file_lists), 2)
        self.assertEqual(dialog.file_lists[0].item(0).text(), "modified.txt")
        self.assertEqual(dialog.file_lists[1].item(0).text(), "new.txt")
        self.assertEqual(dialog.selected_paths(), [])
        self.assertFalse(dialog.action_button.isEnabled())
        dialog.file_lists[1].item(0).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(dialog.selected_paths(), [str(Path(changes.root) / "new.txt")])
        self.assertTrue(dialog.action_button.isEnabled())
        dialog.file_lists[1].item(0).setCheckState(Qt.CheckState.Unchecked)
        self.assertFalse(dialog.action_button.isEnabled())

    def test_unstaging_dialog_unstages_only_unchecked_files(self):
        from PySide6.QtCore import Qt
        from src.dialogs import GitStagingDialog
        from src.git_operations import GitChange, GitChanges

        changes = GitChanges(self.temporary.name, [], [], [GitChange("keep.txt"), GitChange("unstage.txt")])
        dialog = GitStagingDialog(changes, "unstage", self.window)
        self.addCleanup(dialog.close)
        self.assertEqual(len(dialog.file_lists), 1)
        self.assertTrue(all(
            dialog.file_lists[0].item(row).checkState() == Qt.CheckState.Checked for row in range(2)
        ))
        self.assertFalse(dialog.action_button.isEnabled())
        dialog.file_lists[0].item(1).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(dialog.selected_paths(), [str(Path(changes.root) / "unstage.txt")])
        self.assertTrue(dialog.action_button.isEnabled())

    def test_empty_dialogs_do_not_allow_operations(self):
        from src.dialogs import GitStagingDialog
        from src.git_operations import GitChanges

        for action in ("stage", "unstage"):
            with self.subTest(action=action):
                dialog = GitStagingDialog(GitChanges(self.temporary.name, [], [], []), action, self.window)
                self.assertEqual(dialog.selected_paths(), [])
                self.assertFalse(dialog.action_button.isEnabled())
                dialog.close()

    def test_dialog_passes_both_rename_paths(self):
        from PySide6.QtCore import Qt
        from src.dialogs import GitStagingDialog
        from src.git_operations import GitChange, GitChanges

        changes = GitChanges(self.temporary.name, [], [], [GitChange("new.txt", "old.txt")])
        dialog = GitStagingDialog(changes, "unstage", self.window)
        self.addCleanup(dialog.close)
        dialog.file_lists[0].item(0).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(dialog.selected_paths(), [
            str(Path(changes.root) / "new.txt"), str(Path(changes.root) / "old.txt")
        ])

    def test_dialog_acceptance_runs_selected_operation_at_repository_root(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QDialog
        from src.dialogs import GitStagingDialog
        from src.file_explorer import _GitWorker
        from src.git_operations import GitChange, GitChanges

        changes = GitChanges(self.temporary.name, [GitChange("modified.txt")], [], [GitChange("staged.txt")])
        for action, operation, path in (
            ("staging", "stage", "modified.txt"), ("unstaging", "unstage", "staged.txt")
        ):
            with self.subTest(action=action):
                self.window._git_worker = _GitWorker(self.temporary.name, action, [], "")

                def accept_selection(dialog):
                    dialog.file_lists[0].item(0).setCheckState(
                        Qt.CheckState.Checked if operation == "stage" else Qt.CheckState.Unchecked
                    )
                    return QDialog.Accepted

                with patch.object(GitStagingDialog, "exec", accept_selection), patch.object(
                    self.window, "_start_git_operation"
                ) as start:
                    self.window._git_operation_finished(changes, True)
                    start.assert_called_once_with(operation, changes.root, [str(Path(changes.root) / path)])

    def test_closing_staging_dialog_does_not_change_index(self):
        from PySide6.QtWidgets import QDialog
        from src.dialogs import GitStagingDialog
        from src.file_explorer import _GitWorker
        from src.git_operations import GitChanges

        self.window._git_worker = _GitWorker(self.temporary.name, "staging", [], "")
        with patch.object(GitStagingDialog, "exec", return_value=QDialog.Rejected), patch.object(
            self.window, "_start_git_operation"
        ) as start:
            self.window._git_operation_finished(GitChanges(self.temporary.name, [], [], []), True)
            start.assert_not_called()

    def test_worker_reports_repository_status_on_gui_thread(self):
        from PySide6.QtCore import QEventLoop, QTimer

        GitOperations(self.temporary.name).run("init")
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        with patch.object(self.window, "_show_git_output") as output:
            self.window._start_git_operation("status", self.temporary.name, [])
            self.window._git_worker.signals.completed.connect(loop.quit)
            timer.start(5000)
            loop.exec()
            self.window._git_thread_pool.waitForDone()
            self.application.processEvents()
            output.assert_called_once()
            self.assertIn("No commits yet", output.call_args.args[0])
            self.assertIsNone(self.window._git_worker)

    def test_worker_dialog_workflow_changes_only_chosen_files(self):
        from PySide6.QtCore import QEventLoop, QTimer, Qt
        from src.dialogs import GitStagingDialog

        root = Path(self.temporary.name)
        operations = GitOperations(str(root))
        operations.run("init")
        chosen = root / "chosen.txt"
        untouched = root / "untouched.txt"
        chosen.write_text("chosen content\n")
        untouched.write_text("untouched content\n")
        original_exec = GitStagingDialog.exec

        for action in ("staging", "unstaging"):
            with self.subTest(action=action):
                if action == "unstaging":
                    operations.run("stage", [str(untouched)])
                loop = QEventLoop()
                timer = QTimer()
                timer.setSingleShot(True)
                timer.timeout.connect(loop.quit)

                def apply_dialog(dialog):
                    def select_and_apply():
                        for file_list in dialog.file_lists:
                            for row in range(file_list.count()):
                                item = file_list.item(row)
                                if item.text() == chosen.name:
                                    item.setCheckState(
                                        Qt.CheckState.Checked if action == "staging" else Qt.CheckState.Unchecked
                                    )
                        dialog.action_button.click()

                    QTimer.singleShot(0, select_and_apply)
                    return original_exec(dialog)

                with patch.object(GitStagingDialog, "exec", apply_dialog), patch.object(
                    self.window, "_show_git_output", side_effect=lambda output: loop.quit()
                ) as output, patch("src.file_explorer.QMessageBox.warning", side_effect=lambda *args: loop.quit()) as warning:
                    self.window._start_git_operation(action, str(root), [])
                    timer.start(5000)
                    loop.exec()
                    timer.stop()
                    self.window._git_thread_pool.waitForDone()
                    self.application.processEvents()
                    warning.assert_not_called()
                    output.assert_called_once()
                    self.assertIsNone(self.window._git_worker)
                with Repo(root) as repository:
                    self.assertEqual(
                        {entry[0] for entry in repository.index.entries},
                        {chosen.name} if action == "staging" else {untouched.name},
                    )
                self.assertEqual(chosen.read_text(), "chosen content\n")
                self.assertEqual(untouched.read_text(), "untouched content\n")

    def test_output_is_read_only(self):
        from PySide6.QtWidgets import QPlainTextEdit

        self.window._show_git_output("Repository status")
        text = self.window.findChild(QPlainTextEdit)
        self.assertTrue(text.isReadOnly())
        self.assertEqual(text.toPlainText(), "Repository status")

    def test_status_buttons_use_original_repository_after_navigation(self):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        from src.file_explorer import _GitWorker

        for label, action in (("Push", "push"), ("Pull", "pull")):
            with self.subTest(action=action):
                self.window._git_worker = _GitWorker(self.temporary.name, "status", [], "")
                self.window._git_operation_finished("Repository status", True)
                dialog = next(
                    child for child in self.window.findChildren(QDialog)
                    if child.windowTitle() == "Repository Status" and child.isVisible()
                )
                buttons = dialog.findChild(QDialogButtonBox).buttons()
                self.assertEqual({button.text().replace("&", "") for button in buttons}, {"Push", "Pull", "Close"})
                with patch.object(self.window, "current_path", return_value="/elsewhere"), patch.object(
                    self.window, "_start_git_operation"
                ) as start:
                    next(button for button in buttons if button.text() == label).click()
                    start.assert_called_once_with(action, self.temporary.name, [])
                self.assertFalse(dialog.isVisible())

    def test_other_git_output_has_only_close_button(self):
        from PySide6.QtWidgets import QDialogButtonBox

        self.window._show_git_output("Commit output")
        buttons = self.window.findChild(QDialogButtonBox).buttons()
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].text().replace("&", ""), "Close")


if __name__ == "__main__":
    unittest.main()