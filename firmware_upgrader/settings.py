"""Qt settings and credential storage helpers for the desktop app."""

from __future__ import annotations

from pathlib import Path
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


def default_csv_path() -> str:
    documents = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
    base = Path(documents) if documents else Path.home()
    return str(base / "ADTRAN Firmware Upgrader" / "device_upgrades.csv")


class SettingsStore:
    def __init__(self) -> None:
        self.settings = QSettings(ORG_NAME, APP_NAME)

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
        self.settings.setValue("initial_username", credentials.initial_username.strip())
        self.settings.setValue("upgraded_username", credentials.upgraded_username.strip())
        self._set_secret("initial_password", credentials.initial_password)
        self._set_secret("upgraded_password", credentials.upgraded_password)
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
        return keyring is not None

    def _secret(self, account: str) -> str:
        if keyring is not None:
            value = keyring.get_password(KEYCHAIN_SERVICE, account)
            return value or ""
        fallback = self.settings.value(f"secrets/{account}", "")
        return str(fallback or "")

    def _set_secret(self, account: str, value: Optional[str]) -> None:
        secret = value or ""
        if keyring is not None:
            if secret:
                keyring.set_password(KEYCHAIN_SERVICE, account, secret)
            else:
                try:
                    keyring.delete_password(KEYCHAIN_SERVICE, account)
                except keyring.errors.PasswordDeleteError:
                    pass
            return
        self.settings.setValue(f"secrets/{account}", secret)
