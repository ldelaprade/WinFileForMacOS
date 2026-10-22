import tempfile
import unittest
from pathlib import Path

from unittest.mock import call, patch

from src.file_operations import create_file, move_items_to_trash


class CreateFileTests(unittest.TestCase):
    def test_creates_empty_file(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)

            create_file(parent, "notes.txt")

            created_file = parent / "notes.txt"
            self.assertTrue(created_file.is_file())
            self.assertEqual(created_file.read_bytes(), b"")

    def test_refuses_to_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            existing_file = parent / "notes.txt"
            existing_file.write_text("keep this", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                create_file(parent, existing_file.name)

            self.assertEqual(existing_file.read_text(encoding="utf-8"), "keep this")


class MoveItemsToTrashTests(unittest.TestCase):
    @patch("src.file_operations.send2trash")
    def test_moves_each_item_to_system_trash(self, send_to_trash):
        paths = ["/tmp/one.txt", "/tmp/two.txt"]

        failures = move_items_to_trash(paths)

        self.assertEqual(failures, [])
        self.assertEqual(send_to_trash.call_args_list, [call(path) for path in paths])

    @patch("src.file_operations.send2trash", side_effect=OSError("trash unavailable"))
    def test_reports_trash_failures_without_deleting_items(self, send_to_trash):
        path = "/tmp/keep.txt"

        failures = move_items_to_trash([path])

        self.assertEqual(failures, [f"{path}: trash unavailable"])
        send_to_trash.assert_called_once_with(path)


if __name__ == "__main__":
    unittest.main()