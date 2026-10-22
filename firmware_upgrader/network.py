"""Native adapter discovery for the desktop app, without localized shell output."""

from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network

from PySide6.QtNetwork import QAbstractSocket, QNetworkInterface


@dataclass(frozen=True)
class NetworkAddress:
    name: str
    label: str
    ip: str
    prefix_length: int
    wired: bool

    def reaches(self, device_ip: str) -> bool:
        return IPv4Address(device_ip) in IPv4Network(
            f"{self.ip}/{self.prefix_length}", strict=False
        )


def network_addresses() -> list[NetworkAddress]:
    addresses = []
    flags = QNetworkInterface.InterfaceFlag
    for interface in QNetworkInterface.allInterfaces():
        state = interface.flags()
        if not state & flags.IsUp or not state & flags.IsRunning:
            continue
        if state & (flags.IsLoopBack | flags.IsPointToPoint):
            continue
        for entry in interface.addressEntries():
            if entry.ip().protocol() != QAbstractSocket.NetworkLayerProtocol.IPv4Protocol:
                continue
            ip = IPv4Address(entry.ip().toString())
            if ip.is_loopback or ip.is_unspecified or entry.prefixLength() < 0:
                continue
            addresses.append(NetworkAddress(
                name=interface.name(),
                label=interface.humanReadableName(),
                ip=str(ip),
                prefix_length=entry.prefixLength(),
                wired=interface.type() == QNetworkInterface.InterfaceType.Ethernet,
            ))
    return addresses


def select_connection(
    device_ip: str, interface_name: str = "", local_ip: str = ""
) -> NetworkAddress:
    """Keep a job on its selected adapter, including after DHCP changes its IP."""
    addresses = network_addresses()
    if interface_name:
        addresses = [address for address in addresses if address.name == interface_name]
    if local_ip:
        addresses = [address for address in addresses if address.ip == local_ip]
    elif not interface_name:
        addresses = [address for address in addresses if address.wired]
    candidates = [address for address in addresses if address.reaches(device_ip)]
    if not candidates:
        raise ValueError(
            f"No connected Ethernet address can reach {device_ip}. Check the cable and "
            "selected adapter. Use DHCP or configure a local IPv4 address on the "
            "device's subnet, then refresh the interfaces."
        )
    if len(candidates) > 1:
        raise ValueError(
            "More than one local address can reach the device. Select the connected "
            "adapter and local IP explicitly."
        )
    return candidates[0]
