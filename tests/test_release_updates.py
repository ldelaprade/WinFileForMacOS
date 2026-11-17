import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from src.release_updates import ReleaseNotifier, available_release, release_tag
from src.version import APP_VERSION
from tools.build_release import pack_command


class ReleaseTests(unittest.TestCase):
    def release(self, name="RetroFile-win-x64-Setup.exe", tag="v3.1.0"):
        return {"tag_name": tag, "draft": False, "prerelease": False, "assets": [{"name": name}]}

    def test_flag_must_be_boolean_true(self):
        for policy in (None, [], {}, {"enabled": "true", "tag": "v3.1.0"}, {"enabled": False}):
            self.assertIsNone(release_tag(policy))
        self.assertEqual(release_tag({"enabled": True, "tag": "v3.1.0"}), "v3.1.0")

    def test_new_stable_release_has_fixed_repository_link(self):
        result = available_release(self.release(), "v3.1.0", "3.0.0", "Windows", "AMD64")
        self.assertEqual(result, ("3.1.0", "https://github.com/ldelaprade/WinFileForMacOS/releases/tag/v3.1.0"))

    def test_no_alert_for_old_equal_invalid_or_preview_versions(self):
        for tag in ("v2.9.9", "v3.0.0", "invalid", "v3.1.0rc1", "v3.1.0.dev1"):
            self.assertIsNone(available_release(self.release(tag=tag), tag, "3.0.0", "Windows", "AMD64"))

    def test_draft_prerelease_tag_mismatch_and_missing_assets_are_ignored(self):
        for changes in ({"draft": True}, {"prerelease": True}, {"tag_name": "v9.0.0"}, {"assets": None}):
            self.assertIsNone(available_release(self.release() | changes, "v3.1.0", "3.0.0", "Windows", "AMD64"))

    def test_only_matching_native_installers_trigger_alert(self):
        for system, machine, asset in (
            ("Windows", "AMD64", "RetroFile-win-x64-Setup.exe"),
            ("Darwin", "arm64", "RetroFile-osx-arm64-Setup.pkg"),
            ("Linux", "x86_64", "RetroFile-linux-x64.AppImage"),
        ):
            with self.subTest(system=system):
                self.assertIsNotNone(available_release(self.release(asset), "v3.1.0", "3.0.0", system, machine))
        for asset in ("RetroFile-win-arm64-Setup.exe", "RetroFile-win-x64-Portable.zip", "RetroFile-Setup.exe"):
            self.assertIsNone(available_release(self.release(asset), "v3.1.0", "3.0.0", "Windows", "AMD64"))

    def test_pack_command_uses_native_layout_and_isolated_channels(self):
        for system, machine, runtime, directory in (
            ("Windows", "AMD64", "win-x64", "RetroFile"),
            ("Darwin", "arm64", "osx-arm64", "RetroFile.app"),
            ("Linux", "x86_64", "linux-x64", "RetroFile"),
        ):
            command = pack_command("RetroFile", APP_VERSION, system, machine)
            self.assertEqual(command[command.index("--channel") + 1], runtime)
            self.assertTrue(command[command.index("--packDir") + 1].endswith(directory))

    def test_pack_version_must_match_running_app(self):
        with self.assertRaises(ValueError):
            pack_command("RetroFile", "999.0.0", "Windows", "AMD64")


class ReleaseNotifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.window = QWidget()
        self.addCleanup(self.window.close)
        self.notifier = ReleaseNotifier(self.window)
        self.notifier.settings = QSettings(os.path.join(temporary.name, "updates.ini"), QSettings.Format.IniFormat)

    def test_disabled_startup_does_not_make_requests(self):
        self.notifier.settings.setValue("updates/check_on_startup", False)
        with patch.object(self.notifier, "_get") as request:
            self.notifier.start()
        request.assert_not_called()

    def test_disabled_repository_flag_does_not_query_releases(self):
        with patch.object(self.notifier, "_get") as request:
            self.notifier._policy_received({"enabled": False, "tag": "v3.1.0"})
        request.assert_not_called()

    def test_enabled_flag_queries_only_the_configured_tag(self):
        with patch.object(self.notifier, "_get") as request:
            self.notifier._policy_received({"enabled": True, "tag": "v3.1.0"})
        self.assertEqual(request.call_args.args[0], "https://api.github.com/repos/ldelaprade/WinFileForMacOS/releases/tags/v3.1.0")

    def test_dialog_requires_confirmation_and_preserves_opt_out(self):
        self.notifier.tag = "v999.0.0"
        release = ReleaseTests().release(tag="v999.0.0")
        with patch("src.release_updates.platform.system", return_value="Windows"), patch(
            "src.release_updates.platform.machine", return_value="AMD64"
        ), patch("src.release_updates.QDesktopServices.openUrl") as open_url:
            self.notifier._release_received(release)
            self.assertIsNotNone(self.notifier.dialog)
            open_url.assert_not_called()
            self.notifier.dialog.checkBox().setChecked(False)
            self.notifier.dialog.done(QMessageBox.StandardButton.No)
            open_url.assert_not_called()
            self.assertFalse(self.notifier.settings.value("updates/check_on_startup", True, type=bool))

    def test_view_release_opens_fixed_github_url(self):
        self.notifier.tag = "v999.0.0"
        with patch("src.release_updates.platform.system", return_value="Windows"), patch(
            "src.release_updates.platform.machine", return_value="AMD64"
        ), patch("src.release_updates.QDesktopServices.openUrl") as open_url:
            self.notifier._release_received(ReleaseTests().release(tag="v999.0.0"))
            self.notifier.dialog.done(QMessageBox.StandardButton.Yes)
            self.assertEqual(open_url.call_args.args[0].toString(), "https://github.com/ldelaprade/WinFileForMacOS/releases/tag/v999.0.0")


if __name__ == "__main__":
    unittest.main()