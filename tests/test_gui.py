import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import QSettings
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QScrollArea

from firmware_upgrader.adtran_service import AdtranCredentials, AdtranJobResult, DeviceRecord, read_device_records
from firmware_upgrader.gui import MainWindow, load_firmware_path, save_firmware_path
from firmware_upgrader.settings import CredentialStoreError, SettingsStore


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = SettingsStore(QSettings(str(self.root / "settings.ini"), QSettings.IniFormat))
        self.store.set_output_csv_path(str(self.root / "output.csv"))
        with patch("firmware_upgrader.gui.network_addresses", return_value=[]):
            self.window = MainWindow(self.store)
        self.record = DeviceRecord("2026-10-22 12:00:00", "172.16.192.1", "Office", "secret",
                                   "SER123", "001122334455", "SmartOS 12", "Info Only")

    def tearDown(self):
        self.window.worker = None
        self.window.pending_result = None
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def test_active_worker_blocks_close(self):
        self.window.worker = Mock()
        event = QCloseEvent()
        with patch.object(QMessageBox, "warning") as warning:
            self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        warning.assert_called_once()

    def test_window_fits_small_laptop_height(self):
        self.window.resize(1000, 640)
        self.window.show()
        self.app.processEvents()
        self.assertLessEqual(self.window.height(), 640)
        start = self.window.start_button.mapTo(self.window, self.window.start_button.rect().bottomRight())
        self.assertLess(start.y(), self.window.height())
        scroll = self.window.findChild(QScrollArea)
        self.assertLessEqual(scroll.widget().width(), scroll.viewport().width())

    def test_csv_retry_keeps_start_disabled_and_recovers_record(self):
        result = AdtranJobResult(False, self.record, str(self.root / "locked.csv"), ("Save Record to retry.",))
        with patch.object(QMessageBox, "warning"):
            self.window.on_job_finished(result)
        self.window.worker_finished()
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertEqual(self.window.results_table.rowCount(), 0)
        target = self.root / "recovered.csv"
        with patch("firmware_upgrader.gui.QFileDialog.getSaveFileName", return_value=(str(target), "")):
            self.window.save_pending_record()
        self.assertIsNone(self.window.pending_result)
        self.assertTrue(self.window.start_button.isEnabled())
        self.assertTrue(self.window.next_button.isEnabled())
        self.assertEqual(read_device_records(target), [self.record])
        self.assertEqual(self.window.results_table.item(0, 3).text(), "********")

    def test_cancelled_record_save_preserves_pending_data(self):
        self.window.pending_result = AdtranJobResult(False, self.record, str(self.root / "locked.csv"))
        with patch("firmware_upgrader.gui.QFileDialog.getSaveFileName", return_value=("", "")):
            self.window.save_pending_record()
        self.assertIsNotNone(self.window.pending_result)

    def test_keychain_failure_shows_dialog_instead_of_crashing(self):
        with patch.object(self.store, "credentials", side_effect=CredentialStoreError("Store locked")), \
             patch.object(QMessageBox, "warning") as warning:
            self.assertFalse(self.window.open_settings())
        warning.assert_called_once()

    def test_worker_completion_restores_controls_after_thread_finishes(self):
        self.window.operation_combo.setCurrentIndex(1)
        with patch.object(self.store, "has_credentials", return_value=True), \
             patch.object(self.store, "credentials", return_value=AdtranCredentials("admin", "pw", "root", "pw")), \
             patch("firmware_upgrader.gui.AdtranUpgradeService") as service, \
             patch.object(QMessageBox, "information"):
            service.return_value.run_job.return_value = AdtranJobResult(True, self.record, "output.csv")
            self.window.start_job()
            self.assertFalse(self.window.start_button.isEnabled())
            worker = self.window.worker
            self.assertTrue(worker.wait(2000))
            QTest.qWait(20)
        self.assertIsNone(self.window.worker)
        self.assertTrue(self.window.start_button.isEnabled())
        self.assertEqual(self.window.results_table.rowCount(), 1)

    def test_bundled_firmware_setting_survives_onefile_temp_directory_change(self):
        old = self.root / "_MEIold"
        new = self.root / "_MEInew"
        with patch("firmware_upgrader.gui.resource_path", return_value=old / "firmware_images"):
            setting = save_firmware_path(str(old / "firmware_images" / "test.img"))
        self.assertEqual(setting, "bundled:test.img")
        with patch("firmware_upgrader.gui.resource_path", return_value=new / "firmware_images"):
            self.assertEqual(load_firmware_path(setting), str(new / "firmware_images" / "test.img"))


if __name__ == "__main__":
    unittest.main()
