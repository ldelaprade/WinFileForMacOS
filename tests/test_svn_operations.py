import shutil
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.svn_operations import SvnOperationError, SvnOperations


@unittest.skipUnless(shutil.which("svn") and shutil.which("svnadmin"), "SVN tools are required")
class SvnOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        repository = self.root / "repository"
        subprocess.run(["svnadmin", "create", str(repository)], check=True, capture_output=True)
        self.working = self.root / "working"
        self.other = self.root / "other"
        for directory in (self.working, self.other):
            subprocess.run(
                ["svn", "checkout", "--non-interactive", repository.as_uri(), str(directory)],
                check=True, capture_output=True,
            )
        self.operations = SvnOperations(str(self.working))

    def test_add_commit_diff_and_update_only_chosen_files(self):
        chosen = self.working / "chosen @ file.txt"
        untouched = self.working / "untouched.txt"
        chosen.write_text("initial\n")
        untouched.write_text("not committed\n")
        self.assertIn(chosen.name, self.operations.run("status"))
        self.operations.run("add", [str(chosen)])
        self.operations.run("add", [str(untouched)])
        self.operations.run("commit", [str(chosen)], "Initial selected file")
        other_operations = SvnOperations(str(self.other))
        other_operations.run("update")
        self.assertEqual((self.other / chosen.name).read_text(), "initial\n")
        self.assertFalse((self.other / untouched.name).exists())
        chosen.write_text("changed\n")
        self.assertIn("+changed", self.operations.run("diff", [str(chosen)]))
        self.operations.run("commit", [str(chosen)], "Selected change")
        other_operations.run("update")
        self.assertEqual((self.other / chosen.name).read_text(), "changed\n")

    def test_delete_schedules_versioned_removal_and_removes_selected_unversioned_file(self):
        tracked = self.working / "tracked.txt"
        untracked = self.working / "untracked.txt"
        tracked.write_text("tracked content\n")
        self.operations.run("add", [str(tracked)])
        self.operations.run("commit", [str(tracked)], "Add tracked file")
        untracked.write_text("untracked content\n")

        output = self.operations.run("delete", [str(tracked), str(untracked)])

        self.assertFalse(tracked.exists())
        self.assertFalse(untracked.exists())
        self.assertIn("D", output)
        self.assertIn("tracked.txt", self.operations.run("status"))
        self.operations.run("commit", [str(tracked)], "Delete tracked file")
        SvnOperations(str(self.other)).run("update")
        self.assertFalse((self.other / tracked.name).exists())

    def test_changes_filters_candidates_for_add_delete_and_commit(self):
        tracked = self.working / "tracked.txt"
        scheduled_delete = self.working / "scheduled-delete.txt"
        tracked.write_text("initial\n")
        scheduled_delete.write_text("to be deleted\n")
        self.operations.run("add", [str(tracked), str(scheduled_delete)])
        self.operations.run("commit", [str(tracked), str(scheduled_delete)], "Initial files")
        tracked.write_text("modified\n")
        scheduled_delete.unlink()
        self.operations.run("delete", [str(scheduled_delete)])
        unversioned_file = self.working / "new.txt"
        unversioned_file.write_text("new\n")
        unversioned_folder = self.working / "new folder"
        unversioned_folder.mkdir()

        changes = self.operations.changes()
        statuses = {Path(item.path).name: item.status for item in changes.items}

        self.assertEqual(statuses[tracked.name], "modified")
        self.assertEqual(statuses[scheduled_delete.name], "deleted")
        self.assertEqual(statuses[unversioned_file.name], "unversioned")
        self.assertEqual(statuses[unversioned_folder.name], "unversioned")
        self.assertEqual(
            {Path(item.path).name for item in changes.for_action("add")},
            {unversioned_file.name, unversioned_folder.name},
        )
        self.assertEqual(
            {Path(item.path).name for item in changes.for_action("delete")},
            {tracked.name},
        )
        self.assertEqual(
            {Path(item.path).name for item in changes.for_action("commit")},
            {tracked.name},
        )

    def test_checkout_repository_url_into_explicit_local_folder(self):
        repository = self.root / "repository"
        source = self.root / "source"
        subprocess.run(
            ["svn", "checkout", "--non-interactive", repository.as_uri(), str(source)],
            check=True, capture_output=True,
        )
        source_file = source / "checked out.txt"
        source_file.write_text("repository content\n")
        source_operations = SvnOperations(str(source))
        source_operations.run("add", [str(source_file)])
        source_operations.run("commit", [str(source_file)], "Initial checkout content")

        destination = self.root / "local" / "SVN" / "501010-000200 Conexus FPGA Configuration"
        destination.parent.mkdir(parents=True)
        output = SvnOperations(str(destination.parent)).checkout(
            repository.as_uri(), str(destination)
        )

        self.assertTrue(destination.joinpath("checked out.txt").is_file())
        self.assertIn("checked out.txt", output)
        self.assertEqual(SvnOperations(str(destination)).run("status"), "No local changes.")

    def test_checkout_rejects_invalid_urls_and_nonempty_destination(self):
        destination = self.root / "destination"
        destination.mkdir()
        (destination / "keep.txt").write_text("keep\n")
        operations = SvnOperations(str(self.root))
        for url, path in (
            ("", self.root / "empty-url"),
            ("https://example.com", self.root / "missing-repository-path"),
            ("https://user:password@example.com/svn/project", self.root / "embedded-credentials"),
            ((self.root / "repository").as_uri(), destination),
        ):
            with self.subTest(url=url, path=path), self.assertRaises(SvnOperationError):
                operations.checkout(url, str(path))
        self.assertEqual((destination / "keep.txt").read_text(), "keep\n")

    def test_update_reports_conflict_without_discarding_local_changes(self):
        chosen = self.working / "tracked.txt"
        chosen.write_text("initial\n")
        self.operations.run("add", [str(chosen)])
        self.operations.run("commit", [str(chosen)], "Initial file")
        other_operations = SvnOperations(str(self.other))
        other_operations.run("update")
        other_path = self.other / chosen.name
        other_path.write_text("local change\n")
        chosen.write_text("server change\n")
        self.operations.run("commit", [str(chosen)], "Server change")

        output = other_operations.run("update")

        self.assertIn(chosen.name, output)
        self.assertIn("local change", other_path.read_text())
        self.assertIn("server change", other_path.read_text())
        self.assertIn("C", other_operations.run("status"))

    def test_nested_folder_and_empty_working_copy(self):
        self.assertEqual(self.operations.run("status"), "No local changes.")
        self.assertEqual(self.operations.run("diff"), "No local differences.")
        nested = self.working / "nested"
        nested.mkdir()
        self.operations.run("add", [str(nested)])
        self.operations.run("commit", [str(nested)], "Add folder")
        self.assertEqual(SvnOperations(str(nested)).run("status"), "No local changes.")

    def test_rejects_empty_selection_message_metadata_and_outside_paths(self):
        for action in ("add", "commit"):
            with self.subTest(action=action), self.assertRaises(SvnOperationError):
                self.operations.run(action)
        with self.assertRaises(SvnOperationError):
            self.operations.run("commit", [str(self.working / "file")], "  ")
        for path in (self.other / "file", self.working / ".svn" / "wc.db"):
            with self.subTest(path=path), self.assertRaises(SvnOperationError):
                self.operations.run("add", [str(path)])

    def test_non_working_copy_has_error(self):
        with self.assertRaises(SvnOperationError):
            SvnOperations(str(self.root)).run("status")

    def test_missing_client_and_timeout_have_actionable_errors(self):
        with patch("src.svn_operations.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(SvnOperationError, "Install svn"):
                self.operations.run("status")

    def test_commands_disable_terminal_prompts_and_bound_runtime(self):
        result = subprocess.CompletedProcess([], 0, "Status output\n", "")
        with patch("src.svn_operations.subprocess.run", return_value=result) as run:
            self.assertEqual(self.operations._execute("status", "--", str(self.working)), "Status output")
            self.assertEqual(run.call_args.args[0][:3], ["svn", "status", "--non-interactive"])
            self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(run.call_args.kwargs["timeout"], 120)
        with patch("src.svn_operations.subprocess.run", side_effect=subprocess.TimeoutExpired("svn", 120)):
            with self.assertRaisesRegex(SvnOperationError, "timed out"):
                self.operations.run("status")

    def test_authenticated_commands_pass_credentials_without_caching(self):
        result = subprocess.CompletedProcess([], 0, "Status output\n", "")
        with patch("src.svn_operations.subprocess.run", return_value=result) as run:
            output = SvnOperations(str(self.working), ("svn-user", "svn-secret"))._execute(
                "info", "--xml", str(self.working)
            )
        self.assertEqual(output, "Status output")
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["svn", "info", "--non-interactive"])
        self.assertIn("--no-auth-cache", command)
        self.assertEqual(command[command.index("--username") + 1], "svn-user")
        self.assertEqual(command[command.index("--password") + 1], "svn-secret")

    def test_authentication_failure_is_identified_for_prompt(self):
        result = subprocess.CompletedProcess([], 1, "", "svn: E215004: Authentication failed")
        with patch("src.svn_operations.subprocess.run", return_value=result):
            with self.assertRaises(SvnOperationError) as raised:
                self.operations._execute("info", "--xml", str(self.working))
        self.assertTrue(raised.exception.authentication_required)


class SvnExplorerIntegrationTests(unittest.TestCase):
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

    def test_menu_routes_selected_paths_and_disables_selection_commands(self):
        from PySide6.QtWidgets import QMenu

        for paths in ([], [str(Path(self.temporary.name) / "chosen.txt")]):
            with self.subTest(paths=paths), patch.object(self.window, "selected_paths", return_value=paths):
                menu = QMenu(self.window)
                self.window._add_svn_menu(menu)
                actions = menu.actions()[0].menu().actions()
                self.assertEqual(len(actions), 7)
                self.assertEqual(
                    [actions[index].text() for index in (3, 4, 5)],
                    ["Add from Status...", "Delete from Status...", "Commit from Status..."],
                )
                self.assertTrue(all(actions[index].isEnabled() for index in (3, 4, 5)))
                with patch.object(self.window, "_start_svn_operation") as start:
                    actions[2].trigger()
                    start.assert_called_once_with("diff", self.temporary.name, [])
                    start.reset_mock()
                    actions[6].trigger()
                    start.assert_called_once_with("update", self.temporary.name, [])
                with patch.object(self.window, "_start_svn_selection_operation") as select:
                    for index, action in ((3, "add"), (4, "delete"), (5, "commit")):
                        actions[index].trigger()
                        select.assert_called_with(action, self.temporary.name)
                with patch.object(self.window, "_start_svn_checkout") as checkout:
                    actions[0].trigger()
                    checkout.assert_called_once_with(self.temporary.name)

    def test_selection_dialog_filters_actions_and_selects_only_checked_items(self):
        from PySide6.QtCore import Qt
        from src.dialogs import SvnSelectionDialog
        from src.svn_operations import SvnChange, SvnChanges

        changes = SvnChanges(self.temporary.name, [
            SvnChange(str(Path(self.temporary.name) / "modified.txt"), "modified"),
            SvnChange(str(Path(self.temporary.name) / "new.txt"), "unversioned"),
            SvnChange(str(Path(self.temporary.name) / "deleted.txt"), "deleted"),
        ])
        add_dialog = SvnSelectionDialog(changes, "add", self.window)
        self.addCleanup(add_dialog.close)
        self.assertEqual(add_dialog.file_list.count(), 1)
        self.assertIn("unversioned", add_dialog.file_list.item(0).text().lower())
        self.assertFalse(add_dialog.action_button.isEnabled())
        add_dialog.file_list.item(0).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(add_dialog.selected_paths(), [str(Path(self.temporary.name) / "new.txt")])
        self.assertTrue(add_dialog.action_button.isEnabled())
        add_dialog.uncheck_all_button.click()
        self.assertEqual(add_dialog.selected_paths(), [])

        delete_dialog = SvnSelectionDialog(changes, "delete", self.window)
        self.addCleanup(delete_dialog.close)
        self.assertEqual(delete_dialog.file_list.count(), 1)
        delete_dialog.check_all_button.click()
        self.assertEqual(set(delete_dialog.selected_paths()), {
            str(Path(self.temporary.name) / "modified.txt"),
        })

        commit_dialog = SvnSelectionDialog(changes, "commit", self.window)
        self.addCleanup(commit_dialog.close)
        self.assertEqual(commit_dialog.file_list.count(), 1)
        self.assertIn("modified.txt", commit_dialog.file_list.item(0).text())
        self.assertEqual(commit_dialog.selected_paths(), [])

    def test_background_status_checklist_passes_chosen_paths_to_operation(self):
        from PySide6.QtCore import QEventLoop, QTimer
        from PySide6.QtWidgets import QDialog
        from src.dialogs import SvnSelectionDialog
        from src.svn_operations import SvnChange, SvnChanges

        chosen_path = str(Path(self.temporary.name) / "new file.txt")
        changes = SvnChanges(self.temporary.name, [SvnChange(chosen_path, "unversioned")])
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)

        with patch("src.svn_operations.SvnOperations.changes", return_value=changes) as scan, patch.object(
            SvnSelectionDialog, "exec", return_value=QDialog.DialogCode.Accepted
        ), patch.object(
            SvnSelectionDialog, "selected_paths", return_value=[chosen_path]
        ), patch.object(self.window, "_start_svn_operation", side_effect=lambda *_: loop.quit()) as start:
            self.window._start_svn_selection_operation("add", self.temporary.name)
            timer.start(5000)
            loop.exec()
            timer.stop()
            self.window._svn_thread_pool.waitForDone()
            self.application.processEvents()

        scan.assert_called_once_with()
        start.assert_called_once_with("add", self.temporary.name, [chosen_path])
        self.assertIsNone(self.window._svn_worker)

    def test_checkout_dialog_suggests_repository_name_and_returns_destination(self):
        from PySide6.QtTest import QTest
        from src.dialogs import SvnCheckoutDialog

        dialog = SvnCheckoutDialog(self.temporary.name, self.window)
        self.addCleanup(dialog.close)
        dialog.url_edit.setText(
            "http://ap-svn-01:3660/svn/Engineering%20Parts%20Index/501010-000200%20Conexus%20FPGA%20Configuration/"
        )
        self.assertEqual(dialog.folder_name_edit.text(), "501010-000200 Conexus FPGA Configuration")
        url, destination = dialog.values()
        self.assertEqual(url, dialog.url_edit.text())
        self.assertEqual(
            destination,
            str(Path(self.temporary.name) / "501010-000200 Conexus FPGA Configuration"),
        )
        self.assertTrue(dialog.checkout_button.isEnabled())
        dialog.folder_name_edit.selectAll()
        QTest.keyClicks(dialog.folder_name_edit, "Custom Checkout")
        occupied_destination = Path(self.temporary.name) / "Custom Checkout"
        occupied_destination.mkdir()
        (occupied_destination / "keep.txt").write_text("keep\n")
        dialog.url_edit.setText("https://svn.example.com/another/project/")
        self.assertEqual(dialog.folder_name_edit.text(), "Custom Checkout")
        self.assertFalse(dialog.checkout_button.isEnabled())

    def test_checkout_dialog_acceptance_starts_worker_with_exact_target(self):
        from PySide6.QtWidgets import QDialog
        from src.dialogs import SvnCheckoutDialog

        destination = str(Path(self.temporary.name) / "SVN" / "project")
        with patch.object(SvnCheckoutDialog, "exec", return_value=QDialog.DialogCode.Accepted), patch.object(
            SvnCheckoutDialog, "values", return_value=("https://svn.example.com/project", destination)
        ), patch.object(self.window, "_start_svn_operation") as start:
            self.window._start_svn_checkout(self.temporary.name)
        start.assert_called_once_with(
            "checkout", str(Path(destination).parent), ["https://svn.example.com/project", destination]
        )

    def test_cancelled_or_empty_commit_does_not_start_worker(self):
        for response in (("", False), ("  ", True)):
            with self.subTest(response=response), patch(
                "src.file_explorer.QInputDialog.getMultiLineText", return_value=response
            ), patch("src.file_explorer.QMessageBox.warning"), patch.object(self.window._svn_thread_pool, "start") as start:
                self.window._start_svn_operation("commit", self.temporary.name, ["chosen.txt"])
                start.assert_not_called()
                self.assertIsNone(self.window._svn_worker)

    def test_delete_requires_confirmation_and_starts_with_selected_paths(self):
        from PySide6.QtWidgets import QDialog

        paths = [str(Path(self.temporary.name) / "tracked.txt")]
        with patch("src.dialogs.DeleteConfirmDialog.exec", return_value=QDialog.DialogCode.Rejected), patch.object(
            self.window._svn_thread_pool, "start"
        ) as start:
            self.window._start_svn_operation("delete", self.temporary.name, paths)
        start.assert_not_called()
        self.assertIsNone(self.window._svn_worker)

        with patch("src.dialogs.DeleteConfirmDialog.exec", return_value=QDialog.DialogCode.Accepted), patch.object(
            self.window._svn_thread_pool, "start"
        ) as start:
            self.window._start_svn_operation("delete", self.temporary.name, paths)
        worker = start.call_args.args[0]
        self.assertEqual(worker.action, "delete")
        self.assertEqual(worker.paths, paths)
        self.assertIs(self.window._svn_worker, worker)
        self.window._svn_worker = None

    def test_status_update_button_keeps_original_working_copy(self):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit
        from src.file_explorer import _SvnWorker

        self.window._svn_worker = _SvnWorker(self.temporary.name, "status", [], "")
        self.window._svn_operation_finished("No local changes.", True)
        dialog = self.window.findChild(QDialog)
        self.assertEqual(dialog.windowTitle(), "SVN Working Copy Status")
        self.assertTrue(dialog.findChild(QPlainTextEdit).isReadOnly())
        buttons = dialog.findChild(QDialogButtonBox).buttons()
        self.assertEqual({button.text().replace("&", "") for button in buttons}, {"Update", "Close"})
        with patch.object(self.window, "current_path", return_value="/elsewhere"), patch.object(
            self.window, "_start_svn_operation"
        ) as start:
            next(button for button in buttons if button.text() == "Update").click()
            start.assert_called_once_with("update", self.temporary.name, [])

    def test_failure_clears_worker_and_displays_error(self):
        from src.file_explorer import _SvnWorker

        self.window._svn_worker = _SvnWorker(self.temporary.name, "update", [], "")
        with patch("src.file_explorer.QMessageBox.warning") as warning:
            self.window._svn_operation_finished("Authentication failed", False)
            warning.assert_called_once_with(self.window, "SVN Operation Failed", "Authentication failed")
        self.assertIsNone(self.window._svn_worker)

    def test_authentication_failure_prompts_and_retries_same_operation(self):
        from PySide6.QtWidgets import QLineEdit
        from src.file_explorer import _SvnWorker

        worker = _SvnWorker(self.temporary.name, "commit", ["selected.txt"], "Commit message")
        worker.authentication_required = True
        self.window._svn_worker = worker
        with patch(
            "src.file_explorer.QInputDialog.getText", side_effect=[("svn-user", True), ("svn-secret", True)]
        ) as prompt, patch(
            "src.file_explorer.QInputDialog.getMultiLineText", return_value=("Commit message", True)
        ), patch.object(self.window._svn_thread_pool, "start") as start:
            self.window._svn_operation_finished("Authentication failed", False)
        prompt.assert_any_call(self.window, "SVN Authentication", "Username:")
        self.assertEqual(prompt.call_args.args[2], "Password:")
        self.assertEqual(prompt.call_args.args[3], QLineEdit.EchoMode.Password)
        retry = start.call_args.args[0]
        self.assertEqual(retry.action, "commit")
        self.assertEqual(retry.directory, self.temporary.name)
        self.assertEqual(retry.paths, ["selected.txt"])
        self.assertEqual(retry.message, "Commit message")
        self.assertEqual(retry.credentials, ("svn-user", "svn-secret"))
        self.window._svn_worker = None

    def test_cancelled_authentication_prompt_reports_original_failure(self):
        from src.file_explorer import _SvnWorker

        worker = _SvnWorker(self.temporary.name, "update", [], "")
        worker.authentication_required = True
        self.window._svn_worker = worker
        with patch("src.file_explorer.QInputDialog.getText", return_value=("", False)), patch(
            "src.file_explorer.QMessageBox.warning"
        ) as warning, patch.object(self.window._svn_thread_pool, "start") as start:
            self.window._svn_operation_finished("Authentication failed", False)
        warning.assert_called_once_with(self.window, "SVN Operation Failed", "Authentication failed")
        start.assert_not_called()

    def test_git_and_svn_operations_cannot_overlap(self):
        from src.file_explorer import _GitWorker, _SvnWorker

        self.window._svn_worker = _SvnWorker(self.temporary.name, "update", [], "")
        self.window._start_git_operation("status", self.temporary.name, [])
        self.assertIsNone(self.window._git_worker)
        self.window._svn_worker = None
        self.window._git_worker = _GitWorker(self.temporary.name, "status", [], "")
        self.window._start_svn_operation("status", self.temporary.name, [])
        self.assertIsNone(self.window._svn_worker)
        self.window._git_worker = None

    def test_worker_returns_output_on_gui_thread(self):
        from PySide6.QtCore import QEventLoop, QTimer

        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        with patch("src.svn_operations.SvnOperations.run", return_value="Working copy status") as operation, patch.object(
            self.window, "_show_command_output", side_effect=lambda *args: loop.quit()
        ) as output:
            self.window._start_svn_operation("status", self.temporary.name, [])
            timer.start(5000)
            loop.exec()
            timer.stop()
            self.window._svn_thread_pool.waitForDone()
            self.application.processEvents()
            operation.assert_called_once_with("status", [], "")
            self.assertEqual(output.call_args.args[:2], ("Working copy status", "SVN Working Copy Status"))
            self.assertIsNone(self.window._svn_worker)


if __name__ == "__main__":
    unittest.main()