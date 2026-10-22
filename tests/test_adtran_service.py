import csv
import http.client
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import quote

from firmware_upgrader.adtran_service import (
    CSV_HEADERS, OPERATION_INFO_ONLY, OPERATION_UPGRADE,
    AdtranCredentials, AdtranDependencies, AdtranJob, AdtranServiceError,
    AdtranUpgradeService, AuthenticationError, DeviceRecord, FirmwareHttpServer,
    append_device_record, execute_ssh_command, monitor_upgrade_progress,
    parse_build_info, parse_mfg_info, parse_wifi_config, read_device_records,
    ssh_connect_with_shell,
)
from firmware_upgrader.network import NetworkAddress


def credentials():
    return AdtranCredentials("admin", "initial-pass", "root", "upgraded-pass")


def record():
    return DeviceRecord("2026-10-22 12:00:00", "172.16.192.1", "Office \u00e9", "secret",
                        "SER123", "001122334455", "SmartOS 12", "Upgrade")


class FakeFirmwareServer:
    def __init__(self, firmware_path, port, host):
        self.firmware_path, self.port, self.host = firmware_path, port, host
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False


class FakeRuntime:
    def __init__(self):
        self.commands = []
        self.connections = []
        self.complete = True
        self.ping_ok = True
        self.auth_ok = True
        self.connection_queries = []
        self.server = None
        self.extra_output = {}
        self.closed = 0

    def select(self, ip, interface="", local_ip=""):
        self.connection_queries.append((ip, interface, local_ip))
        return NetworkAddress("adapter-1", "Ethernet", "172.16.192.50" if ip.startswith("172.")
                              else "192.168.1.50", 24, True)

    def connect(self, ip, username=None, source_ip=None, **_kwargs):
        self.connections.append((ip, username, source_ip))
        return (object(), object(), "connected") if self.auth_ok else (None, None, "auth_failed")

    def command(self, _channel, command, **_kwargs):
        self.commands.append(command)
        return self.extra_output.get(command, {
            "show wifi config": "wireless.i5g.ssid='Office'\nwireless.i5g.key='secret'\n",
            "show mfg": "MFG_SERIAL=ADTN12345\nMFG_MAC=AABBCCDDEEFF\n",
            "show buildinfo": "DISTRIB_DESCRIPTION='SmartOS 12.8.3.1'\n",
            "system restore nvram": "Proceed? y/n",
        }.get(command, "ok"))

    def monitor(self, _channel, **_kwargs):
        return True, self.complete, "Upgrade completed" if self.complete else "download failed"

    def server_factory(self, *args, **kwargs):
        self.server = FakeFirmwareServer(*args, **kwargs)
        return self.server

    def close(self, client, _channel):
        if client is not None:
            self.closed += 1

    def dependencies(self):
        return AdtranDependencies(
            select_connection=self.select, wait_for_ping=lambda *_a, **_k: self.ping_ok,
            ssh_connect_with_shell=self.connect, execute_ssh_command=self.command,
            monitor_upgrade_progress=self.monitor, safe_close_ssh_connection=self.close,
            sleep=lambda _seconds: None, firmware_server_factory=self.server_factory,
        )


class ParsingAndCsvTests(unittest.TestCase):
    def test_parsers(self):
        self.assertEqual(parse_wifi_config("wireless.i5g.ssid='Office'\nwireless.i5g.key='secret'"),
                         ("Office", "secret"))
        self.assertEqual(parse_mfg_info("MFG_SERIAL=SER123\nMFG_MAC=001122334455"),
                         ("SER123", "001122334455"))
        self.assertEqual(parse_build_info("DISTRIB_DESCRIPTION='SmartOS 12'"), "SmartOS 12")
        self.assertEqual(parse_mfg_info("show MFG_SERIAL"), ("", ""))

    def test_csv_unicode_empty_file_and_multiple_appends(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "devices.csv"
            path.touch()
            append_device_record(record(), path)
            append_device_record(record(), path)
            with path.open(newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(rows, [CSV_HEADERS, record().to_csv_row(), record().to_csv_row()])
            self.assertEqual(read_device_records(path), [record(), record()])
            self.assertEqual(path.read_bytes().count(b"\xef\xbb\xbf"), 1)

    def test_existing_legacy_utf8_csv_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "devices.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerows([CSV_HEADERS, record().to_csv_row()])
            append_device_record(record(), path)
            self.assertEqual(read_device_records(path), [record(), record()])

    def test_passwords_are_absent_from_dataclass_repr(self):
        self.assertNotIn("initial-pass", repr(credentials()))
        self.assertNotIn("secret", repr(record()))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.firmware = self.root / "adtran.img"
        self.firmware.write_bytes(b"firmware")
        self.csv_path = self.root / "device_upgrades.csv"
        self.runtime = FakeRuntime()
        self.logs = []
        self.service = AdtranUpgradeService(self.runtime.dependencies(), log=self.logs.append)
        self.job = AdtranJob(OPERATION_UPGRADE, credentials(), firmware_path=str(self.firmware),
                             output_csv_path=str(self.csv_path))

    def test_success_tracks_adapter_after_dhcp_change(self):
        result = self.service.run_job(self.job)
        self.assertTrue(result.success)
        self.assertEqual(len(read_device_records(self.csv_path)), 1)
        self.assertIn("system restore default", self.runtime.commands)
        self.assertEqual(self.runtime.connections[-1], ("172.16.192.1", "root", "172.16.192.50"))
        self.assertIn(("172.16.192.1", "adapter-1", ""), self.runtime.connection_queries)
        self.assertFalse(self.runtime.server.started)
        self.assertEqual(self.runtime.server.host, "192.168.1.50")
        self.assertEqual(self.runtime.closed, 2)

    def test_info_only_works_without_console_streams(self):
        with patch.object(sys, "stdout", None), patch.object(sys, "stderr", None):
            result = self.service.run_job(replace(self.job, operation=OPERATION_INFO_ONLY))
        self.assertTrue(result.success)
        self.assertEqual(result.record.operation_type, "Info Only")
        self.assertIsNone(self.runtime.server)

    def test_auth_failure_does_not_retry_rejected_credentials(self):
        self.runtime.auth_ok = False
        with self.assertRaisesRegex(AuthenticationError, "Authentication failed"):
            self.service.run_job(self.job)
        self.assertEqual(len(self.runtime.connections), 2)
        self.assertEqual(self.runtime.commands, [])

    def test_ping_timeout_stops_before_any_ssh_commands(self):
        self.runtime.ping_ok = False
        with self.assertRaisesRegex(AdtranServiceError, "did not respond"):
            self.service.run_job(self.job)
        self.assertEqual(self.runtime.connections, [])

    def test_missing_and_empty_firmware_stop_before_device_access(self):
        for path in (self.root / "absent.img", self.root / "empty.img"):
            if path.name == "empty.img":
                path.touch()
            with self.subTest(path=path), self.assertRaises(AdtranServiceError):
                self.service.run_job(replace(self.job, firmware_path=str(path)))
        self.assertEqual(self.runtime.connections, [])

    def test_unconfirmed_upgrade_never_resets_or_records_success(self):
        self.runtime.complete = False
        with self.assertRaisesRegex(AdtranServiceError, "completion was not confirmed"):
            self.service.run_job(self.job)
        self.assertNotIn("system restore default", self.runtime.commands)
        self.assertEqual(read_device_records(self.csv_path), [])
        self.assertFalse(self.runtime.server.started)
        self.assertEqual(self.runtime.closed, 1)

    def test_confirmation_response_is_included_in_monitoring(self):
        self.runtime.extra_output[f"upgrade http://192.168.1.50:8000/adtran.img"] = "Proceed? y/n"
        self.runtime.extra_output["y"] = "Upgrade completed successfully"
        monitor = Mock(return_value=(True, True, "Upgrade completed successfully"))
        self.service.dependencies.monitor_upgrade_progress = monitor
        self.service.run_job(self.job)
        self.assertIn("Upgrade completed successfully", monitor.call_args.kwargs["initial_output"])

    def test_csv_preflight_failure_prevents_flashing(self):
        self.csv_path.write_text("wrong,columns\n", encoding="utf-8")
        with self.assertRaisesRegex(AdtranServiceError, "different CSV columns"):
            self.service.run_job(self.job)
        self.assertEqual(self.runtime.commands, [])

    def test_csv_becoming_locked_preserves_record_for_save_retry(self):
        with patch("firmware_upgrader.adtran_service.append_device_record", side_effect=PermissionError):
            result = self.service.run_job(self.job)
        self.assertFalse(result.success)
        self.assertEqual(result.record.serial_number, "ADTN12345")
        self.assertIn("Save Record", result.warnings[0])
        self.assertEqual(read_device_records(self.csv_path), [])

    def test_incomplete_extraction_cannot_be_recorded_as_complete(self):
        self.runtime.extra_output["show mfg"] = "unrecognized command"
        with self.assertRaisesRegex(AdtranServiceError, "information is incomplete"):
            self.service.run_job(replace(self.job, operation=OPERATION_INFO_ONLY))
        self.assertEqual(read_device_records(self.csv_path), [])

    def test_ipv4_validation_prevents_command_injection(self):
        with self.assertRaisesRegex(AdtranServiceError, "valid device-network IPv4"):
            self.service.run_job(replace(self.job, device_ip="192.168.1.1 & whoami"))
        self.assertEqual(self.runtime.connections, [])

    def test_logs_redact_credentials_and_device_password_fields(self):
        self.service.run_job(replace(self.job, operation=OPERATION_INFO_ONLY))
        self.service.log("initial-pass upgraded-pass secret")
        self.service.log("wireless.i5g.key='unknown-secret'\npassword=another-secret")
        logs = "\n".join(self.logs)
        for secret in ("initial-pass", "upgraded-pass", "secret", "unknown-secret", "another-secret"):
            self.assertNotIn(secret, logs)


class StreamChannel:
    def __init__(self, chunks=()):
        self.chunks = list(chunks)
        self.sent = []

    @property
    def closed(self):
        return not self.chunks

    def recv_ready(self):
        return bool(self.chunks)

    def recv(self, _size):
        return self.chunks.pop(0)

    def sendall(self, command):
        self.sent.append(command)


class SshTests(unittest.TestCase):
    def test_upgrade_completion_read_before_monitoring_is_not_lost(self):
        self.assertTrue(monitor_upgrade_progress(StreamChannel(), initial_output="Upgrade completed successfully")[1])

    def test_split_packets_are_recognized(self):
        self.assertTrue(monitor_upgrade_progress(StreamChannel([b"Upgrade com", b"pleted successfully\n"]))[1])

    def test_download_success_and_negated_success_do_not_count_as_flash_success(self):
        for output in ("Download complete", "Firmware download completed", "Upgrade not complete", "Upgrade incomplete"):
            with self.subTest(output=output):
                self.assertFalse(monitor_upgrade_progress(StreamChannel(), initial_output=output)[1])

    def test_failure_overrides_success_in_same_response(self):
        self.assertFalse(monitor_upgrade_progress(StreamChannel(), initial_output="Download complete\nFlash failed")[1])

    def test_buffered_failure_is_read_before_accepting_success(self):
        self.assertFalse(monitor_upgrade_progress(StreamChannel([b"Upgrade complete\n", b"Flash failed\n"]))[1])

    def test_closed_channel_still_delivers_buffered_completion(self):
        channel = Mock(closed=True)
        channel.recv_ready.side_effect = [True, False]
        channel.recv.return_value = b"Upgrade completed successfully\n"
        self.assertTrue(monitor_upgrade_progress(channel)[1])

    def test_monitor_timeout_is_not_success(self):
        channel = Mock(closed=False)
        channel.recv_ready.return_value = False
        self.assertFalse(monitor_upgrade_progress(channel, timeout=0)[1])

    def test_sensitive_command_output_is_not_logged_even_across_packets(self):
        log = Mock()
        channel = StreamChannel([b"wireless.i5g.key='sec", b"ret'\n"])
        output = execute_ssh_command(channel, "show wifi config", log=log, log_output=False)
        self.assertIn("secret", output)
        self.assertEqual(log.call_count, 1)
        self.assertEqual(channel.sent, ["show wifi config\n"])

    def test_bound_socket_failure_closes_client_without_unbound_fallback(self):
        with patch("firmware_upgrader.adtran_service._open_socket", side_effect=OSError("bind failed")), \
             patch("firmware_upgrader.adtran_service.paramiko.SSHClient") as client:
            result = ssh_connect_with_shell("192.168.1.1", "admin", "pw", source_ip="192.168.1.50")
        self.assertEqual(result[2], "connection_failed")
        client.return_value.close.assert_called_once()
        client.return_value.connect.assert_not_called()


class FirmwareServerTests(unittest.TestCase):
    def test_only_selected_file_is_served_without_console_streams(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "firmware \u00e9.img"
            image.write_bytes(b"test firmware")
            (root / "device_upgrades.csv").write_text("private", encoding="utf-8")
            server = FirmwareHttpServer(image, 0)
            try:
                server.start()
                for method, path, status in (
                    ("GET", "/" + quote(image.name), 200),
                    ("HEAD", "/" + quote(image.name), 200),
                    ("GET", "/device_upgrades.csv", 404),
                    ("GET", "/", 404),
                    ("GET", "/../device_upgrades.csv", 404),
                ):
                    with self.subTest(method=method, path=path):
                        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=2)
                        try:
                            with patch.object(sys, "stderr", None), patch.object(sys, "stdout", None):
                                connection.request(method, path)
                                response = connection.getresponse()
                                body = response.read()
                            self.assertEqual(response.status, status)
                            if method == "GET" and status == 200:
                                self.assertEqual(body, b"test firmware")
                        finally:
                            connection.close()
            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main()
