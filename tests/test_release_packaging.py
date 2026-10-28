import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools import package_desktop


class ReleasePackagingTests(unittest.TestCase):
    def test_windows_archive_allowlist_and_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dist").mkdir()
            (root / "packaging").mkdir()
            (root / "dist" / "ADTRAN Firmware Upgrader.exe").write_bytes(b"test executable")
            (root / "dist" / "device_upgrades.csv").write_text("private device data")
            (root / ".env").write_text("private credentials")
            for name in ("OPERATOR_README.md", "TEST_BUILD_README.md"):
                (root / "packaging" / name).write_text(name)
            with patch.object(package_desktop, "ROOT", root), \
                 patch.object(package_desktop.sys, "platform", "win32"), \
                 patch.object(package_desktop.platform, "machine", return_value="AMD64"), \
                 patch.object(package_desktop.platform, "platform", return_value="Windows-test"), \
                 patch.object(package_desktop.subprocess, "check_output", side_effect=["abc123\n", b""]), \
                 patch.object(package_desktop, "distributions", return_value=[]):
                package_desktop.main()
            archive = next((root / "dist" / "releases").glob("*.zip"))
            with zipfile.ZipFile(archive) as bundle:
                names = {Path(name).name for name in bundle.namelist()}
                self.assertEqual(names, {"ADTRAN Firmware Upgrader.exe", "OPERATOR_README.md",
                                         "TEST_BUILD_README.md", "BUILD_INFO.json", "SHA256SUMS.txt"})
                prefix = archive.stem + "/"
                metadata = json.loads(bundle.read(prefix + "BUILD_INFO.json"))
                self.assertEqual(metadata["source_commit"], "abc123")
                self.assertFalse(metadata["tracked_changes"])
                for line in bundle.read(prefix + "SHA256SUMS.txt").decode().splitlines():
                    expected, name = line.split("  ", 1)
                    self.assertEqual(hashlib.sha256(bundle.read(prefix + name)).hexdigest(), expected)
            self.assertEqual(archive.with_suffix(".zip.sha256").read_text().split()[0],
                             package_desktop.sha256(archive))


if __name__ == "__main__":
    unittest.main()
