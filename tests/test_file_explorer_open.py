import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.file_explorer import ExplorerWindow


class FileExplorerOpenTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX execute permissions are required")
    def test_open_launches_executable_file(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "tool"
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o755)

            custom_library_path = str(Path(directory) / "custom-libraries")
            with patch.dict(
                os.environ,
                {
                    "LD_LIBRARY_PATH": os.pathsep.join(
                        ("/snap/core20/current/lib/x86_64-linux-gnu", custom_library_path)
                    )
                },
            ):
                with (
                    patch("src.file_explorer.subprocess.Popen") as popen,
                    patch("src.file_explorer.QDesktopServices.openUrl") as open_url,
                ):
                    ExplorerWindow._open_file(object(), str(executable))

            popen.assert_called_once()
            self.assertEqual(popen.call_args.args[0], [str(executable)])
            self.assertEqual(popen.call_args.kwargs["cwd"], directory)
            self.assertEqual(
                popen.call_args.kwargs["env"]["LD_LIBRARY_PATH"], custom_library_path
            )
            open_url.assert_not_called()

    def test_open_uses_desktop_association_for_non_executable_file(self):
        with tempfile.TemporaryDirectory() as directory:
            document = Path(directory) / "notes.txt"
            document.write_text("notes\n")

            with (
                patch("src.file_explorer.subprocess.Popen") as popen,
                patch("src.file_explorer.QDesktopServices.openUrl") as open_url,
            ):
                ExplorerWindow._open_file(object(), str(document))

            popen.assert_not_called()
            self.assertEqual(open_url.call_args.args[0].toLocalFile(), str(document))

    def test_windows_user_folder_path_reads_registry_value(self):
        import sys

        registry = MagicMock()
        registry.HKEY_CURRENT_USER = object()
        registry.OpenKey.return_value.__enter__.return_value = object()
        registry.QueryValueEx.return_value = (r"%USERPROFILE%\Redirected", 2)

        with (
            patch("src.file_explorer.sys.platform", "win32"),
            patch.dict(sys.modules, {"winreg": registry}),
            patch("src.file_explorer.os.path.expandvars", return_value=r"C:\Users\Me\Redirected"),
        ):
            path = ExplorerWindow._user_folder_path("Desktop")

        self.assertEqual(path, r"C:\Users\Me\Redirected")
        registry.OpenKey.assert_called_once_with(
            registry.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        )
        registry.QueryValueEx.assert_called_once_with(
            registry.OpenKey.return_value.__enter__.return_value,
            "Desktop",
        )

    def test_windows_download_path_uses_downloads_known_folder_value(self):
        import sys

        registry = MagicMock()
        registry.HKEY_CURRENT_USER = object()
        registry.OpenKey.return_value.__enter__.return_value = object()
        registry.QueryValueEx.return_value = (r"C:\Users\Me\Cloud\Downloads", 2)

        with (
            patch("src.file_explorer.sys.platform", "win32"),
            patch.dict(sys.modules, {"winreg": registry}),
        ):
            path = ExplorerWindow._user_folder_path("Downloads")

        self.assertEqual(path, r"C:\Users\Me\Cloud\Downloads")
        registry.QueryValueEx.assert_called_once_with(
            registry.OpenKey.return_value.__enter__.return_value,
            "{374DE290-123F-4565-9164-39C4925E467B}",
        )


if __name__ == "__main__":
    unittest.main()