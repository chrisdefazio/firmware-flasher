import csv
import tempfile
import unittest
from pathlib import Path

from firmware_upgrader.adtran_service import (
    OPERATION_INFO_ONLY,
    OPERATION_UPGRADE,
    AdtranCredentials,
    AdtranDependencies,
    AdtranJob,
    AdtranServiceError,
    AdtranUpgradeService,
    DeviceRecord,
    append_device_record,
    parse_build_info,
    parse_mfg_info,
    parse_wifi_config,
    read_device_records,
)


class FakeClient:
    pass


class FakeChannel:
    pass


class FakeAdtranRuntime:
    def __init__(self, connect_ok=True, upgrade_complete=True):
        self.connect_ok = connect_ok
        self.upgrade_complete = upgrade_complete
        self.commands = []
        self.connect_usernames = []
        self.cleared_keys = []
        self.closed = 0

    def dependencies(self):
        return AdtranDependencies(
            get_wired_interface_ip=lambda: ("Ethernet", "192.168.1.50"),
            get_gateway_for_connection=lambda _iface, _ip: "192.168.1.1",
            wait_for_ping=lambda *_args, **_kwargs: True,
            clear_ssh_key=self.clear_ssh_key,
            ssh_connect_with_shell=self.ssh_connect_with_shell,
            execute_ssh_command=self.execute_ssh_command,
            monitor_upgrade_progress=self.monitor_upgrade_progress,
            safe_close_ssh_connection=self.safe_close_ssh_connection,
            sleep=lambda _seconds: None,
            firmware_server_factory=FakeFirmwareServer,
        )

    def clear_ssh_key(self, ip):
        self.cleared_keys.append(ip)

    def ssh_connect_with_shell(self, _ip, username=None, **_kwargs):
        self.connect_usernames.append(username)
        if not self.connect_ok:
            return None, None, "auth_failed"
        return FakeClient(), FakeChannel(), "connected"

    def execute_ssh_command(self, _channel, command, *_args, **_kwargs):
        self.commands.append(command)
        if command == "show wifi config":
            return "wireless.i5g.ssid='Office-834'\nwireless.i5g.key='supersecret'\n"
        if command == "show mfg":
            return "MFG_SERIAL=ADTN12345\nMFG_MAC=AABBCCDDEEFF\n"
        if command == "show buildinfo":
            return "DISTRIB_DESCRIPTION='SmartOS 12.8.3.1'\n"
        if command == "system restore nvram":
            return "Proceed? y/n"
        return "ok"

    def monitor_upgrade_progress(self, _channel):
        return True, self.upgrade_complete, "monitor output"

    def safe_close_ssh_connection(self, _ssh_client, _channel):
        self.closed += 1


class FakeFirmwareServer:
    def __init__(self, _directory, port=8000):
        self.port = 8765 if port == 0 else port
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False


def credentials():
    return AdtranCredentials(
        initial_username="admin",
        initial_password="initial-pass",
        upgraded_username="root",
        upgraded_password="upgraded-pass",
    )


class AdtranParsingTests(unittest.TestCase):
    def test_parse_wifi_config(self):
        output = "wireless.i5g.ssid='Office'\nwireless.i5g.key='secret'\n"
        self.assertEqual(parse_wifi_config(output), ("Office", "secret"))

    def test_parse_mfg_info(self):
        output = "MFG_SERIAL=SER123\nMFG_MAC=001122334455\n"
        self.assertEqual(parse_mfg_info(output), ("SER123", "001122334455"))

    def test_parse_build_info(self):
        output = "DISTRIB_DESCRIPTION='SmartOS 12'\n"
        self.assertEqual(parse_build_info(output), "SmartOS 12")

    def test_append_and_read_device_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "device_upgrades.csv"
            record = DeviceRecord(
                timestamp="2026-06-18 12:00:00",
                device_ip="172.16.192.1",
                ssid="Office",
                wifi_password="secret",
                serial_number="SER123",
                mac_address="001122334455",
                firmware_version="SmartOS 12",
                operation_type="Upgrade",
            )
            append_device_record(record, csv_path)

            with csv_path.open(newline="") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(rows[0][0], "Timestamp")
            self.assertEqual(rows[1], record.to_csv_row())

            records = read_device_records(csv_path)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].serial_number, "SER123")


class AdtranServiceTests(unittest.TestCase):
    def test_info_only_service_records_csv(self):
        runtime = FakeAdtranRuntime()
        with tempfile.TemporaryDirectory() as temp_dir:
            job = AdtranJob(
                operation=OPERATION_INFO_ONLY,
                credentials=credentials(),
                local_ip="192.168.1.50",
                device_ip="192.168.1.1",
                output_csv_path=str(Path(temp_dir) / "device_upgrades.csv"),
            )

            result = AdtranUpgradeService(dependencies=runtime.dependencies()).run_job(job)

            self.assertTrue(result.success)
            self.assertIsNotNone(result.record)
            self.assertEqual(result.record.serial_number, "ADTN12345")
            self.assertIn("show wifi config", runtime.commands)
            self.assertTrue(Path(result.csv_path).exists())

    def test_auth_failure_raises_clear_error(self):
        runtime = FakeAdtranRuntime(connect_ok=False)
        job = AdtranJob(
            operation=OPERATION_INFO_ONLY,
            credentials=credentials(),
            local_ip="192.168.1.50",
            device_ip="192.168.1.1",
        )

        with self.assertRaisesRegex(AdtranServiceError, "Failed to connect"):
            AdtranUpgradeService(dependencies=runtime.dependencies()).run_job(job)

    def test_missing_firmware_raises_clear_error(self):
        runtime = FakeAdtranRuntime()
        job = AdtranJob(
            operation=OPERATION_UPGRADE,
            credentials=credentials(),
            firmware_path="/does/not/exist.img",
            local_ip="192.168.1.50",
            device_ip="192.168.1.1",
        )

        with self.assertRaisesRegex(AdtranServiceError, "Firmware file not found"):
            AdtranUpgradeService(dependencies=runtime.dependencies()).run_job(job)

    def test_upgrade_warning_when_completion_not_confirmed(self):
        runtime = FakeAdtranRuntime(upgrade_complete=False)
        with tempfile.TemporaryDirectory() as temp_dir:
            firmware_path = Path(temp_dir) / "adtran.img"
            firmware_path.write_bytes(b"firmware")
            job = AdtranJob(
                operation=OPERATION_UPGRADE,
                credentials=credentials(),
                firmware_path=str(firmware_path),
                local_ip="192.168.1.50",
                device_ip="192.168.1.1",
                upgraded_device_ip="172.16.192.1",
                output_csv_path=str(Path(temp_dir) / "device_upgrades.csv"),
                http_port=0,
            )

            result = AdtranUpgradeService(dependencies=runtime.dependencies()).run_job(job)

            self.assertTrue(result.success)
            self.assertTrue(result.warnings)
            self.assertIn("system restore default", runtime.commands)
            self.assertEqual(result.record.device_ip, "172.16.192.1")


if __name__ == "__main__":
    unittest.main()
