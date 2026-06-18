"""PySide6 desktop GUI for the ADTRAN firmware upgrader."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QAbstractItemView,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from network_utils import get_network_interfaces

from firmware_upgrader import __version__
from firmware_upgrader.adtran_service import (
    CSV_HEADERS,
    OPERATION_INFO_ONLY,
    OPERATION_UPGRADE,
    AdtranCredentials,
    AdtranJob,
    AdtranJobResult,
    AdtranUpgradeService,
    DeviceRecord,
)
from firmware_upgrader.settings import SettingsStore


def resource_path(relative_path: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base_path / relative_path


def app_icon() -> QIcon:
    if QGuiApplication.instance() is None:
        return QIcon()
    icon_path = resource_path("assets/icon/adtran_modem_icon_256.png")
    return QIcon(str(icon_path))


class AdtranWorker(QThread):
    log_message = Signal(str)
    progress_changed = Signal(int, str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, job: AdtranJob):
        super().__init__()
        self.job = job

    def run(self) -> None:
        service = AdtranUpgradeService(
            log=self.log_message.emit,
            progress=self.progress_changed.emit,
        )
        try:
            result = service.run_job(self.job)
        except Exception as exc:  # pragma: no cover - exercised by GUI runtime
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit(result)


class SettingsDialog(QDialog):
    def __init__(self, store: SettingsStore, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.resize(520, 320)

        credentials = store.credentials()

        self.initial_username = QLineEdit(credentials.initial_username)
        self.initial_password = QLineEdit(credentials.initial_password)
        self.initial_password.setEchoMode(QLineEdit.Password)
        self.upgraded_username = QLineEdit(credentials.upgraded_username)
        self.upgraded_password = QLineEdit(credentials.upgraded_password)
        self.upgraded_password.setEchoMode(QLineEdit.Password)

        self.device_ip = QLineEdit(store.device_ip())
        self.upgraded_device_ip = QLineEdit(store.upgraded_device_ip())
        self.output_csv = QLineEdit(store.output_csv_path())
        browse_csv = QPushButton("Browse")
        browse_csv.clicked.connect(self.choose_csv_path)

        csv_row = QWidget()
        csv_layout = QHBoxLayout(csv_row)
        csv_layout.setContentsMargins(0, 0, 0, 0)
        csv_layout.addWidget(self.output_csv)
        csv_layout.addWidget(browse_csv)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.addRow("Initial username", self.initial_username)
        form.addRow("Initial password", self.initial_password)
        form.addRow("Upgraded username", self.upgraded_username)
        form.addRow("Upgraded password", self.upgraded_password)
        form.addRow("Default device IP", self.device_ip)
        form.addRow("Default upgraded IP", self.upgraded_device_ip)
        form.addRow("Output CSV", csv_row)

        keyring_label = QLabel(
            "Passwords are stored in the operating system credential store."
            if store.keyring_available()
            else "Keyring is unavailable. Install the keyring package before using saved passwords."
        )
        keyring_label.setWordWrap(True)
        keyring_label.setObjectName("hintLabel")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(keyring_label)
        layout.addStretch()
        layout.addWidget(buttons)

    def choose_csv_path(self) -> None:
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Choose output CSV",
            self.output_csv.text(),
            "CSV files (*.csv);;All files (*)",
        )
        if filename:
            self.output_csv.setText(filename)

    def accept(self) -> None:
        credentials = AdtranCredentials(
            initial_username=self.initial_username.text().strip(),
            initial_password=self.initial_password.text(),
            upgraded_username=self.upgraded_username.text().strip(),
            upgraded_password=self.upgraded_password.text(),
        )
        try:
            self.store.save_credentials(credentials)
        except Exception as exc:
            QMessageBox.critical(self, "Could not save credentials", str(exc))
            return
        self.store.set_device_ip(self.device_ip.text())
        self.store.set_upgraded_device_ip(self.upgraded_device_ip.text())
        self.store.set_output_csv_path(self.output_csv.text())
        super().accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.store = SettingsStore()
        self.worker: Optional[AdtranWorker] = None

        self.setWindowTitle("ADTRAN Firmware Upgrader")
        self.setWindowIcon(app_icon())
        self.resize(1160, 760)

        self.operation_combo = QComboBox()
        self.operation_combo.addItem("Upgrade firmware and record info", OPERATION_UPGRADE)
        self.operation_combo.addItem("Record device info only", OPERATION_INFO_ONLY)
        self.operation_combo.currentIndexChanged.connect(self.update_operation_controls)

        self.firmware_path = QLineEdit(self.store.firmware_path())
        self.firmware_path.setPlaceholderText("Choose an ADTRAN firmware image")
        self.browse_firmware_button = QPushButton("Browse")
        self.browse_firmware_button.clicked.connect(self.choose_firmware)

        self.interface_combo = QComboBox()
        self.refresh_interfaces_button = QPushButton("Refresh")
        self.refresh_interfaces_button.clicked.connect(self.refresh_interfaces)

        self.device_ip = QLineEdit(self.store.device_ip())
        self.upgraded_device_ip = QLineEdit(self.store.upgraded_device_ip())
        self.output_csv = QLineEdit(self.store.output_csv_path())
        self.browse_csv_button = QPushButton("Browse")
        self.browse_csv_button.clicked.connect(self.choose_output_csv)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("statusBadge")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

        self.start_button = QPushButton("Start Device")
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self.start_job)
        self.next_button = QPushButton("Next Device")
        self.next_button.clicked.connect(self.prepare_next_device)
        self.next_button.setEnabled(False)
        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self.open_settings)

        self.open_csv_button = QPushButton("Open CSV")
        self.open_csv_button.clicked.connect(self.open_csv)
        self.export_csv_button = QPushButton("Export CSV")
        self.export_csv_button.clicked.connect(self.export_csv)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setPlaceholderText("Live device log")

        self.results_table = QTableWidget(0, len(CSV_HEADERS))
        self.results_table.setHorizontalHeaderLabels(CSV_HEADERS)
        self.results_table.horizontalHeader().setStretchLastSection(True)
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_table.setAlternatingRowColors(True)

        self.setCentralWidget(self.build_layout())
        self.refresh_interfaces()
        self.update_operation_controls()
        self.apply_style()

    def build_layout(self) -> QWidget:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(14)

        header = QFrame()
        header.setObjectName("header")
        header_layout = QHBoxLayout(header)
        title = QLabel("ADTRAN Firmware Upgrader")
        title.setObjectName("title")
        subtitle = QLabel("One connected device at a time")
        subtitle.setObjectName("subtitle")
        metadata = QLabel(f"Created by: Chris DeFazio • Version {__version__}")
        metadata.setObjectName("metadataLabel")
        title_column = QVBoxLayout()
        title_column.addWidget(title)
        title_column.addWidget(subtitle)
        title_column.addWidget(metadata)
        header_layout.addLayout(title_column)
        header_layout.addStretch()
        header_layout.addWidget(self.status_label)
        root_layout.addWidget(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.build_controls_panel())
        splitter.addWidget(self.build_work_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root_layout.addWidget(splitter, 1)

        results_group = QGroupBox("Completed devices this session")
        results_layout = QVBoxLayout(results_group)
        results_actions = QHBoxLayout()
        results_actions.addStretch()
        results_actions.addWidget(self.open_csv_button)
        results_actions.addWidget(self.export_csv_button)
        results_layout.addLayout(results_actions)
        results_layout.addWidget(self.results_table)
        root_layout.addWidget(results_group, 1)

        return root

    def build_controls_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(360)
        panel.setMaximumWidth(430)
        layout = QVBoxLayout(panel)
        layout.setSpacing(12)

        job_group = QGroupBox("Job")
        job_form = QFormLayout(job_group)
        job_form.addRow("Operation", self.operation_combo)

        firmware_row = QWidget()
        firmware_layout = QHBoxLayout(firmware_row)
        firmware_layout.setContentsMargins(0, 0, 0, 0)
        firmware_layout.addWidget(self.firmware_path, 1)
        firmware_layout.addWidget(self.browse_firmware_button)
        job_form.addRow("Firmware", firmware_row)
        layout.addWidget(job_group)

        connection_group = QGroupBox("Connection")
        connection_layout = QGridLayout(connection_group)
        connection_layout.addWidget(QLabel("Interface"), 0, 0)
        connection_layout.addWidget(self.interface_combo, 0, 1)
        connection_layout.addWidget(self.refresh_interfaces_button, 0, 2)
        connection_layout.addWidget(QLabel("Device IP"), 1, 0)
        connection_layout.addWidget(self.device_ip, 1, 1, 1, 2)
        connection_layout.addWidget(QLabel("Upgraded IP"), 2, 0)
        connection_layout.addWidget(self.upgraded_device_ip, 2, 1, 1, 2)
        layout.addWidget(connection_group)

        output_group = QGroupBox("Output")
        output_layout = QGridLayout(output_group)
        output_layout.addWidget(QLabel("CSV file"), 0, 0)
        output_layout.addWidget(self.output_csv, 0, 1)
        output_layout.addWidget(self.browse_csv_button, 0, 2)
        layout.addWidget(output_group)

        action_row = QHBoxLayout()
        action_row.addWidget(self.settings_button)
        action_row.addStretch()
        action_row.addWidget(self.next_button)
        action_row.addWidget(self.start_button)
        layout.addLayout(action_row)
        layout.addStretch()

        return panel

    def build_work_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(12)

        progress_group = QGroupBox("Progress")
        progress_layout = QVBoxLayout(progress_group)
        progress_layout.addWidget(self.progress)
        layout.addWidget(progress_group)

        log_group = QGroupBox("Live log")
        log_layout = QVBoxLayout(log_group)
        log_layout.addWidget(self.log_view)
        layout.addWidget(log_group, 1)

        return panel

    def refresh_interfaces(self) -> None:
        current_ip = self.selected_local_ip()
        self.interface_combo.clear()
        self.interface_combo.addItem("Auto detect wired Ethernet", ("", ""))
        try:
            interfaces = get_network_interfaces()
        except Exception as exc:
            self.append_log(f"Could not list network interfaces: {exc}")
            interfaces = []
        for iface, ip in interfaces:
            self.interface_combo.addItem(f"{iface}: {ip}", (iface, ip))

        if current_ip:
            for index in range(self.interface_combo.count()):
                _iface, ip = self.interface_combo.itemData(index)
                if ip == current_ip:
                    self.interface_combo.setCurrentIndex(index)
                    break

    def selected_local_ip(self) -> str:
        data = self.interface_combo.currentData()
        if not data:
            return ""
        _iface, ip = data
        return ip

    def selected_interface_name(self) -> str:
        data = self.interface_combo.currentData()
        if not data:
            return ""
        iface, _ip = data
        return iface

    def update_operation_controls(self) -> None:
        is_upgrade = self.operation_combo.currentData() == OPERATION_UPGRADE
        self.firmware_path.setEnabled(is_upgrade)
        self.browse_firmware_button.setEnabled(is_upgrade)
        self.upgraded_device_ip.setEnabled(is_upgrade)

    def choose_firmware(self) -> None:
        filename, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Choose ADTRAN firmware image",
            self.firmware_path.text() or "firmware_images",
            "Firmware images (*.img *.bin);;All files (*)",
        )
        if filename:
            self.firmware_path.setText(filename)
            self.store.set_firmware_path(filename)

    def choose_output_csv(self) -> None:
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Choose output CSV",
            self.output_csv.text(),
            "CSV files (*.csv);;All files (*)",
        )
        if filename:
            self.output_csv.setText(filename)
            self.store.set_output_csv_path(filename)

    def open_settings(self) -> bool:
        dialog = SettingsDialog(self.store, self)
        accepted = dialog.exec() == QDialog.Accepted
        if accepted:
            self.device_ip.setText(self.store.device_ip())
            self.upgraded_device_ip.setText(self.store.upgraded_device_ip())
            self.output_csv.setText(self.store.output_csv_path())
        return accepted

    def start_job(self) -> None:
        try:
            job = self.build_job()
        except ValueError as exc:
            QMessageBox.warning(self, "Check job setup", str(exc))
            return

        self.set_running(True)
        self.log_view.clear()
        self.progress.setValue(0)
        self.status_label.setText("Running")
        self.append_log("Starting ADTRAN job.")

        self.worker = AdtranWorker(job)
        self.worker.log_message.connect(self.append_log)
        self.worker.progress_changed.connect(self.on_progress)
        self.worker.finished_ok.connect(self.on_job_finished)
        self.worker.failed.connect(self.on_job_failed)
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()

    def build_job(self) -> AdtranJob:
        if not self.store.has_credentials():
            if not self.open_settings() or not self.store.has_credentials():
                raise ValueError("Enter initial and upgraded SSH credentials in Settings.")

        operation = str(self.operation_combo.currentData())
        firmware_path = self.firmware_path.text().strip()
        if operation == OPERATION_UPGRADE:
            if not firmware_path:
                raise ValueError("Choose a firmware image before starting an upgrade.")
            if not Path(firmware_path).expanduser().is_file():
                raise ValueError(f"Firmware file not found: {firmware_path}")

        output_csv = self.output_csv.text().strip()
        if not output_csv:
            raise ValueError("Choose an output CSV path.")

        self.store.set_device_ip(self.device_ip.text())
        self.store.set_upgraded_device_ip(self.upgraded_device_ip.text())
        self.store.set_output_csv_path(output_csv)
        self.store.set_firmware_path(firmware_path)

        return AdtranJob(
            operation=operation,
            credentials=self.store.credentials(),
            firmware_path=firmware_path,
            interface_name=self.selected_interface_name(),
            local_ip=self.selected_local_ip(),
            device_ip=self.device_ip.text().strip() or "192.168.1.1",
            upgraded_device_ip=self.upgraded_device_ip.text().strip() or "172.16.192.1",
            output_csv_path=output_csv,
        )

    def set_running(self, running: bool) -> None:
        for widget in (
            self.operation_combo,
            self.firmware_path,
            self.browse_firmware_button,
            self.interface_combo,
            self.refresh_interfaces_button,
            self.device_ip,
            self.upgraded_device_ip,
            self.output_csv,
            self.browse_csv_button,
            self.settings_button,
            self.start_button,
        ):
            widget.setEnabled(not running)
        if not running:
            self.update_operation_controls()
        self.next_button.setEnabled(not running and self.results_table.rowCount() > 0)

    def on_progress(self, percent: int, message: str) -> None:
        self.progress.setValue(percent)
        self.status_label.setText(message)

    def on_job_finished(self, result: AdtranJobResult) -> None:
        self.set_running(False)
        self.progress.setValue(100)
        self.status_label.setText("Complete")
        for warning in result.warnings:
            self.append_log(f"Warning: {warning}")
        if result.record is not None:
            self.add_record(result.record)
            self.append_log(f"Saved device data to {result.csv_path}")
        QMessageBox.information(self, "Device complete", "The ADTRAN job finished successfully.")

    def on_job_failed(self, message: str) -> None:
        self.set_running(False)
        self.status_label.setText("Failed")
        self.append_log(f"ERROR: {message}")
        QMessageBox.critical(self, "ADTRAN job failed", message)

    def worker_finished(self) -> None:
        self.worker = None

    def prepare_next_device(self) -> None:
        self.progress.setValue(0)
        self.status_label.setText("Ready for next device")
        self.log_view.clear()
        self.append_log("Connect the next ADTRAN device, then start a new job.")
        self.start_button.setFocus()

    def add_record(self, record: DeviceRecord) -> None:
        row = self.results_table.rowCount()
        self.results_table.insertRow(row)
        for column, value in enumerate(record.to_csv_row()):
            item = QTableWidgetItem(value)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.results_table.setItem(row, column, item)
        self.results_table.resizeColumnsToContents()
        self.next_button.setEnabled(True)

    def append_log(self, message: str) -> None:
        if not message:
            return
        self.log_view.appendPlainText(message)
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def open_csv(self) -> None:
        path = Path(self.output_csv.text()).expanduser()
        if not path.exists():
            QMessageBox.information(self, "CSV not found", "No CSV has been written yet.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def export_csv(self) -> None:
        source = Path(self.output_csv.text()).expanduser()
        if not source.exists():
            QMessageBox.information(self, "CSV not found", "No CSV has been written yet.")
            return
        target, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export CSV",
            str(source.name),
            "CSV files (*.csv);;All files (*)",
        )
        if not target:
            return
        try:
            shutil.copyfile(source, target)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        QMessageBox.information(self, "CSV exported", f"Saved CSV to {target}")

    def apply_style(self) -> None:
        QApplication.instance().setStyleSheet(
            """
            QWidget {
                font-size: 13px;
            }
            QMainWindow, QWidget {
                background: #f6f7f9;
                color: #18202a;
            }
            QGroupBox {
                background: #ffffff;
                border: 1px solid #d7dce2;
                border-radius: 8px;
                margin-top: 16px;
                padding: 14px 10px 10px 10px;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 4px;
            }
            QLineEdit, QComboBox, QPlainTextEdit, QTableWidget {
                background: #ffffff;
                border: 1px solid #c8d0da;
                border-radius: 6px;
                padding: 7px;
                selection-background-color: #0f766e;
            }
            QPlainTextEdit {
                font-family: Menlo, Consolas, monospace;
                font-size: 12px;
            }
            QPushButton {
                background: #edf1f5;
                border: 1px solid #c7d0d9;
                border-radius: 6px;
                padding: 8px 12px;
            }
            QPushButton:hover {
                background: #e4ebf2;
            }
            QPushButton:disabled {
                color: #8a95a3;
                background: #eef1f4;
            }
            QPushButton#primaryButton {
                background: #0f766e;
                color: #ffffff;
                border-color: #0f766e;
                font-weight: 600;
            }
            QPushButton#primaryButton:hover {
                background: #115e59;
            }
            QProgressBar {
                border: 1px solid #c8d0da;
                border-radius: 6px;
                background: #ffffff;
                height: 18px;
                text-align: center;
            }
            QProgressBar::chunk {
                border-radius: 5px;
                background: #0f766e;
            }
            QFrame#header {
                background: #ffffff;
                border: 1px solid #d7dce2;
                border-radius: 8px;
            }
            QLabel#title {
                font-size: 24px;
                font-weight: 700;
            }
            QLabel#subtitle, QLabel#metadataLabel, QLabel#hintLabel {
                color: #566273;
            }
            QLabel#metadataLabel {
                font-size: 12px;
            }
            QLabel#statusBadge {
                background: #f3c969;
                color: #2f2410;
                border-radius: 6px;
                padding: 8px 12px;
                font-weight: 600;
            }
            """
        )


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("ADTRAN Firmware Upgrader")
    app.setOrganizationName("FirmwareTools")
    app.setWindowIcon(app_icon())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
