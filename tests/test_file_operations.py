import tempfile
import unittest
from pathlib import Path

from src.file_operations import create_file


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


if __name__ == "__main__":
    unittest.main()