import unittest
from unittest.mock import Mock, patch

from PySide6.QtNetwork import QHostAddress, QNetworkAddressEntry, QNetworkInterface

from firmware_upgrader.network import NetworkAddress, network_addresses, select_connection
from network_utils import wait_for_ping


class NetworkTests(unittest.TestCase):
    def test_native_adapter_names_and_ipv4_filtering(self):
        ipv4 = QNetworkAddressEntry()
        ipv4.setIp(QHostAddress("192.168.1.50"))
        ipv4.setPrefixLength(24)
        ipv6 = QNetworkAddressEntry()
        ipv6.setIp(QHostAddress("fe80::1"))
        adapter = Mock()
        adapter.flags.return_value = QNetworkInterface.IsUp | QNetworkInterface.IsRunning
        adapter.name.return_value = "{stable-adapter-id}"
        adapter.humanReadableName.return_value = "Ethernet \u00e9"
        adapter.type.return_value = QNetworkInterface.Ethernet
        adapter.addressEntries.return_value = [ipv4, ipv6]
        with patch("firmware_upgrader.network.QNetworkInterface.allInterfaces", return_value=[adapter]):
            addresses = network_addresses()
        self.assertEqual(addresses, [NetworkAddress("{stable-adapter-id}", "Ethernet \u00e9", "192.168.1.50", 24, True)])

    def test_auto_detect_excludes_wifi_and_selects_target_subnet(self):
        addresses = [
            NetworkAddress("wifi", "Wi-Fi", "192.168.1.20", 24, False),
            NetworkAddress("eth1", "Ethernet", "10.0.0.20", 24, True),
            NetworkAddress("eth2", "USB Ethernet", "192.168.1.50", 24, True),
        ]
        with patch("firmware_upgrader.network.network_addresses", return_value=addresses):
            self.assertEqual(select_connection("192.168.1.1"), addresses[2])

    def test_multiple_adapters_require_selection(self):
        addresses = [NetworkAddress(f"eth{i}", f"LAN {i}", f"192.168.1.{i}", 24, True) for i in (20, 21)]
        with patch("firmware_upgrader.network.network_addresses", return_value=addresses):
            with self.assertRaisesRegex(ValueError, "More than one"):
                select_connection("192.168.1.1")
            self.assertEqual(select_connection("192.168.1.1", "eth21"), addresses[1])

    def test_reconnect_never_switches_to_another_adapter(self):
        addresses = [NetworkAddress("different-adapter", "LAN", "172.16.192.50", 24, True)]
        with patch("firmware_upgrader.network.network_addresses", return_value=addresses):
            with self.assertRaisesRegex(ValueError, "No connected Ethernet"):
                select_connection("172.16.192.1", "original-adapter")

    def test_manual_local_address_must_still_exist(self):
        with patch("firmware_upgrader.network.network_addresses", return_value=[]):
            with self.assertRaises(ValueError):
                select_connection("192.168.1.1", local_ip="192.168.1.50")

    def test_windows_ping_has_no_shell_or_console_and_is_bounded(self):
        with patch("network_utils.platform.system", return_value="Windows"), \
             patch("network_utils.subprocess.run", return_value=Mock(returncode=0)) as run:
            self.assertTrue(wait_for_ping("192.168.1.1", source_ip="192.168.1.50", log=lambda _m: None))
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["ping", "-n", "1", "-w", "1000", "-S", "192.168.1.50", "192.168.1.1"])
        self.assertFalse(kwargs.get("shell", False))
        self.assertEqual(kwargs["creationflags"], 0x08000000)
        self.assertLessEqual(kwargs["timeout"], 3)

    def test_ping_rejects_shell_metacharacters(self):
        with patch("network_utils.subprocess.run") as run:
            with self.assertRaises(ValueError):
                wait_for_ping("192.168.1.1 & whoami")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
