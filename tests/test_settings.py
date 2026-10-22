import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import QSettings

from firmware_upgrader.adtran_service import AdtranCredentials
from firmware_upgrader.settings import CredentialStoreError, SettingsStore


class SettingsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.settings = QSettings(str(Path(directory.name) / "settings.ini"), QSettings.IniFormat)
        self.store = SettingsStore(self.settings)
        self.backend = Mock()
        self.backend.get_password.return_value = None
        self.store._backend = self.backend

    def test_passwords_are_written_only_to_keyring(self):
        self.store.save_credentials(AdtranCredentials("admin", "initial-secret", "root", "new-secret"))
        self.assertEqual(self.backend.set_password.call_count, 2)
        self.assertEqual(self.settings.value("initial_username"), "admin")
        self.assertFalse(self.settings.contains("secrets/initial_password"))
        self.assertNotIn("secret", Path(self.settings.fileName()).read_text(encoding="utf-8"))

    def test_locked_keyring_errors_are_clear_and_do_not_expose_secrets(self):
        self.backend.set_password.side_effect = RuntimeError("sensitive-backend-error")
        with self.assertRaises(CredentialStoreError) as error:
            self.store.save_credentials(AdtranCredentials("admin", "secret", "root", "secret"))
        self.assertNotIn("sensitive-backend-error", str(error.exception))
        self.assertFalse(self.settings.contains("secrets/initial_password"))
        self.backend.get_password.side_effect = RuntimeError("locked")
        with self.assertRaisesRegex(CredentialStoreError, "Could not read"):
            self.store.credentials()

    def test_no_keyring_never_falls_back_to_plaintext(self):
        self.store._backend = None
        with patch("firmware_upgrader.settings.keyring", None):
            self.assertFalse(self.store.keyring_available())
            with self.assertRaises(CredentialStoreError):
                self.store.save_credentials(AdtranCredentials("admin", "secret"))
        self.assertFalse(self.settings.contains("secrets/initial_password"))

    def test_legacy_password_is_removed_after_secure_migration(self):
        self.settings.setValue("secrets/initial_password", "old-secret")
        self.assertEqual(self.store.credentials().initial_password, "old-secret")
        self.assertFalse(self.settings.contains("secrets/initial_password"))
        self.backend.set_password.assert_called_once_with("adtran-firmware-upgrader", "initial_password", "old-secret")

    def test_failed_migration_is_not_treated_as_a_saved_credential(self):
        self.settings.setValue("secrets/initial_password", "old-secret")
        self.backend.set_password.side_effect = RuntimeError("locked")
        with self.assertRaises(CredentialStoreError):
            self.store.credentials()
        self.assertTrue(self.settings.contains("secrets/initial_password"))


if __name__ == "__main__":
    unittest.main()
