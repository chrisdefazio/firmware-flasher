"""Qt settings and credential storage helpers for the desktop app."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Optional

from PySide6.QtCore import QSettings, QStandardPaths

from firmware_upgrader.adtran_service import AdtranCredentials

try:
    import keyring
except ImportError:  # pragma: no cover - exercised only without optional dependency
    keyring = None


ORG_NAME = "FirmwareTools"
APP_NAME = "ADTRAN Firmware Upgrader"
KEYCHAIN_SERVICE = "adtran-firmware-upgrader"


class CredentialStoreError(ValueError):
    """The OS credential store could not be accessed."""


def default_csv_path() -> str:
    documents = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
    base = Path(documents) if documents else Path.home()
    return str(base / "ADTRAN Firmware Upgrader" / "device_upgrades.csv")


class SettingsStore:
    def __init__(self, settings: Optional[QSettings] = None) -> None:
        self.settings = settings if settings is not None else QSettings(ORG_NAME, APP_NAME)
        self._backend = None

    def device_ip(self) -> str:
        return str(self.settings.value("device_ip", "192.168.1.1"))

    def set_device_ip(self, value: str) -> None:
        self.settings.setValue("device_ip", value.strip() or "192.168.1.1")

    def upgraded_device_ip(self) -> str:
        return str(self.settings.value("upgraded_device_ip", "172.16.192.1"))

    def set_upgraded_device_ip(self, value: str) -> None:
        self.settings.setValue("upgraded_device_ip", value.strip() or "172.16.192.1")

    def firmware_path(self) -> str:
        return str(self.settings.value("firmware_path", ""))

    def set_firmware_path(self, value: str) -> None:
        self.settings.setValue("firmware_path", value.strip())

    def output_csv_path(self) -> str:
        return str(self.settings.value("output_csv_path", default_csv_path()))

    def set_output_csv_path(self, value: str) -> None:
        self.settings.setValue("output_csv_path", value.strip() or default_csv_path())

    def credentials(self) -> AdtranCredentials:
        return AdtranCredentials(
            initial_username=str(self.settings.value("initial_username", "")),
            initial_password=self._secret("initial_password"),
            upgraded_username=str(self.settings.value("upgraded_username", "")),
            upgraded_password=self._secret("upgraded_password"),
        )

    def save_credentials(self, credentials: AdtranCredentials) -> None:
        self._set_secret("initial_password", credentials.initial_password)
        self._set_secret("upgraded_password", credentials.upgraded_password)
        self.settings.setValue("initial_username", credentials.initial_username.strip())
        self.settings.setValue("upgraded_username", credentials.upgraded_username.strip())
        self.settings.remove("secrets")
        self.settings.sync()

    def has_credentials(self) -> bool:
        credentials = self.credentials()
        return bool(
            credentials.initial_username
            and credentials.initial_password
            and credentials.upgraded_username
            and credentials.upgraded_password
        )

    def keyring_available(self) -> bool:
        try:
            self._credential_backend()
            return True
        except CredentialStoreError:
            return False

    def _credential_backend(self):
        if self._backend is not None:
            return self._backend
        try:
            if keyring is None:
                raise RuntimeError("keyring is not installed")
            if sys.platform == "win32":
                from keyring.backends.Windows import WinVaultKeyring
                backend = WinVaultKeyring()
            elif sys.platform == "darwin":
                from keyring.backends.macOS import Keyring
                backend = Keyring()
            else:
                backend = keyring.get_keyring()
            if backend.priority <= 0:
                raise RuntimeError("No credential backend")
            self._backend = backend
            return backend
        except Exception as exc:
            raise CredentialStoreError(
                "The operating system credential store is unavailable. Unlock your "
                "login session and try again. Passwords will not be saved in plain text."
            ) from exc

    def _secret(self, account: str) -> str:
        try:
            backend = self._credential_backend()
            value = backend.get_password(KEYCHAIN_SERVICE, account)
            # Migrate passwords written by older versions only after secure storage succeeds.
            fallback_key = f"secrets/{account}"
            legacy = self.settings.value(fallback_key, "")
            if legacy:
                if not value:
                    backend.set_password(KEYCHAIN_SERVICE, account, str(legacy))
                    value = str(legacy)
                self.settings.remove(fallback_key)
                self.settings.sync()
            return value or ""
        except CredentialStoreError:
            raise
        except Exception as exc:
            raise CredentialStoreError(
                "Could not read saved passwords. Unlock the operating system "
                "credential store and reopen Settings."
            ) from exc

    def _set_secret(self, account: str, value: Optional[str]) -> None:
        secret = value or ""
        try:
            backend = self._credential_backend()
            if secret:
                backend.set_password(KEYCHAIN_SERVICE, account, secret)
            else:
                try:
                    backend.delete_password(KEYCHAIN_SERVICE, account)
                except keyring.errors.PasswordDeleteError:
                    pass
        except CredentialStoreError:
            raise
        except Exception as exc:
            raise CredentialStoreError(
                "Could not save passwords in the operating system credential store. "
                "Unlock it and try again."
            ) from exc
