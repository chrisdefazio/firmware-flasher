import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import build_windows_onefile as build


class WindowsBuildTests(unittest.TestCase):
    def test_build_does_not_embed_csv_credentials_or_unrelated_files(self):
        with tempfile.TemporaryDirectory(prefix="firmware with spaces ") as directory:
            root = Path(directory)
            for name in ("adtran image.img", "OTHER.BIN", "device_upgrades.csv", ".env", "notes.txt"):
                (root / name).touch()
            with patch.object(build, "FIRMWARE_IMAGES_DIR", root):
                command = build.build_command()
            self.assertIn("--onefile", command)
            self.assertIn("--windowed", command)
            self.assertIn("keyring.backends.Windows", command)
            self.assertIn("PySide6.QtNetwork", command)
            self.assertIn(build.add_data_arg(root / "adtran image.img", "firmware_images"), command)
            self.assertIn(build.add_data_arg(root / "OTHER.BIN", "firmware_images"), command)
            for name in ("device_upgrades.csv", ".env", "notes.txt"):
                self.assertNotIn(str(root / name), " ".join(command))

    def test_missing_firmware_directory_is_optional(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(build, "FIRMWARE_IMAGES_DIR", Path(directory) / "absent"):
                command = build.build_command()
        self.assertEqual(command.count("--add-data"), 1)

    def test_windows_data_separator_preserves_paths_with_spaces(self):
        with patch.object(build.os, "pathsep", ";"):
            self.assertEqual(build.add_data_arg(Path("firmware with spaces.img"), "firmware_images"),
                             "firmware with spaces.img;firmware_images")


if __name__ == "__main__":
    unittest.main()
