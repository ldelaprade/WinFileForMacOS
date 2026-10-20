import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.file_explorer import ExplorerWindow


class FileExplorerOpenTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX execute permissions are required")
    def test_open_launches_executable_file(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "tool"
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o755)

            with (
                patch("src.file_explorer.subprocess.Popen") as popen,
                patch("src.file_explorer.QDesktopServices.openUrl") as open_url,
            ):
                ExplorerWindow._open_file(object(), str(executable))

            popen.assert_called_once_with([str(executable)], cwd=directory)
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


if __name__ == "__main__":
    unittest.main()