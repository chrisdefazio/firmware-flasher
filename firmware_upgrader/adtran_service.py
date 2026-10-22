"""ADTRAN desktop workflow with explicit logging and injectable device I/O."""

from __future__ import annotations

import csv
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv4Address
from pathlib import Path
from typing import Callable, Optional, Union
from urllib.parse import quote, unquote, urlsplit

import paramiko

from firmware_upgrader.network import NetworkAddress, select_connection
from network_utils import wait_for_ping


CSV_HEADERS = [
    "Timestamp", "Device IP", "WiFi SSID", "WiFi Password", "Serial Number",
    "MAC Address", "Firmware Version", "Operation Type",
]
OPERATION_UPGRADE = "upgrade"
OPERATION_INFO_ONLY = "info_only"
LogCallback = Callable[[str], None]
ProgressCallback = Callable[[int, str], None]


class AdtranServiceError(RuntimeError):
    """Raised when an ADTRAN workflow cannot complete."""


class AuthenticationError(AdtranServiceError):
    """The device rejected every configured credential pair."""


@dataclass(frozen=True)
class AdtranCredentials:
    initial_username: str = ""
    initial_password: str = field(default="", repr=False)
    upgraded_username: str = ""
    upgraded_password: str = field(default="", repr=False)

    def candidates_for_ip(self, ip: str) -> list[tuple[str, str, str]]:
        initial = (self.initial_username, self.initial_password, "initial")
        upgraded = (self.upgraded_username, self.upgraded_password, "upgraded")
        ordered = [upgraded, initial] if ip.startswith("172.16.192.") else [initial, upgraded]
        candidates = []
        seen = set()
        for username, password, label in ordered:
            if username and password and (username, password) not in seen:
                candidates.append((username, password, label))
                seen.add((username, password))
        return candidates


@dataclass(frozen=True)
class AdtranJob:
    operation: str
    credentials: AdtranCredentials
    firmware_path: str = ""
    interface_name: str = ""
    local_ip: str = ""
    device_ip: str = "192.168.1.1"
    upgraded_device_ip: str = "172.16.192.1"
    output_csv_path: str = "device_upgrades.csv"
    http_port: int = 8000


@dataclass(frozen=True)
class DeviceRecord:
    timestamp: str
    device_ip: str
    ssid: str
    wifi_password: str = field(repr=False)
    serial_number: str
    mac_address: str
    firmware_version: str
    operation_type: str

    def to_csv_row(self) -> list[str]:
        return [
            self.timestamp, self.device_ip, self.ssid, self.wifi_password,
            self.serial_number, self.mac_address, self.firmware_version, self.operation_type,
        ]


@dataclass(frozen=True)
class AdtranJobResult:
    success: bool
    record: Optional[DeviceRecord] = None
    csv_path: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _quiet_log(_message: str) -> None:
    pass


def _open_socket(ip: str, source_ip: Optional[str], timeout: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        if source_ip:
            sock.bind((source_ip, 0))
        sock.connect((ip, 22))
        return sock
    except Exception:
        sock.close()
        raise


def _open_shell(client, timeout):
    channel = client.invoke_shell()
    channel.settimeout(timeout)
    time.sleep(2)
    while channel.recv_ready():
        if not channel.recv(4096):
            break
    return channel


def ssh_connect_with_shell(
    ip: str, username: Optional[str] = None, password: Optional[str] = None,
    timeout: int = 10, source_ip: Optional[str] = None, log: LogCallback = _quiet_log,
) -> tuple[Optional[object], Optional[object], str]:
    """Never fall back to another adapter if the selected source cannot connect."""
    if not username or not password:
        return None, None, "missing_credentials"
    client = paramiko.SSHClient()
    # Bench devices reuse IPs. This client never loads or edits the user's known_hosts.
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sock = None
    try:
        sock = _open_socket(ip, source_ip, timeout)
        client.connect(
            ip, sock=sock, username=username, password=password, timeout=timeout,
            banner_timeout=timeout, auth_timeout=timeout,
            allow_agent=False, look_for_keys=False,
        )
        channel = _open_shell(client, timeout)
        return client, channel, "connected"
    except paramiko.AuthenticationException:
        client.close()
        if sock is not None:
            sock.close()
        return _try_keyboard_interactive(ip, username, password, timeout, source_ip, log)
    except (OSError, paramiko.SSHException) as exc:
        log(f"SSH connection unavailable: {exc}")
        client.close()
        if sock is not None:
            sock.close()
        return None, None, "connection_failed"
    except Exception:
        client.close()
        if sock is not None:
            sock.close()
        raise


def _try_keyboard_interactive(ip, username, password, timeout, source_ip, log):
    sock = None
    transport = None
    client = None
    connected = False
    try:
        sock = _open_socket(ip, source_ip, timeout)
        transport = paramiko.Transport(sock)
        transport.banner_timeout = timeout
        transport.auth_timeout = timeout
        transport.start_client(timeout=timeout)
        transport.auth_interactive(
            username, lambda _title, _instructions, prompts: [password for _ in prompts]
        )
        if not transport.is_authenticated():
            raise paramiko.AuthenticationException("Authentication rejected")
        client = paramiko.SSHClient()
        client._transport = transport
        channel = _open_shell(client, timeout)
        connected = True
        return client, channel, "connected"
    except paramiko.AuthenticationException:
        error = "auth_failed"
    except (OSError, paramiko.SSHException) as exc:
        log(f"SSH connection unavailable: {exc}")
        error = "connection_failed"
    finally:
        # A successfully opened channel owns the transport until the job closes it.
        if not connected:
            if client is not None:
                client.close()
            if transport is not None:
                transport.close()
            if sock is not None:
                sock.close()
    return None, None, error


def execute_ssh_command(
    channel: object, command: str, wait_time: float = 5, max_output_wait: float = 30,
    log: LogCallback = _quiet_log, log_output: bool = True,
) -> str:
    log(f">>> {command}")
    channel.sendall(command + "\n")
    output = bytearray()
    started = last_received = time.monotonic()
    while time.monotonic() - started < max_output_wait:
        if channel.recv_ready():
            chunk = channel.recv(4096)
            if not chunk:
                break
            output.extend(chunk)
            last_received = time.monotonic()
        elif channel.closed or time.monotonic() - last_received >= wait_time:
            break
        else:
            time.sleep(0.1)
    text = output.decode("utf-8", errors="replace")
    if log_output and text.strip():
        log(text.rstrip())
    return text


_UPGRADE_FAILURE = re.compile(
    r"\b(?:error|failed|failure|aborted|unable to|invalid (?:image|firmware))\b", re.I
)
_UPGRADE_SUCCESS = re.compile(
    r"\b(?:upgraded?|firmware(?:\s+(?:upgraded?|updated?))?|flash(?:ed|ing)?)"
    r"(?:\s+(?:is|has|been|operation))*\s*[:.-]?\s+"
    r"(?:completed?|successful(?:ly)?|succeeded)\b"
    r"|\bsuccessfully\s+(?:upgraded|flashed)\b", re.I
)


def monitor_upgrade_progress(
    channel: object, timeout: int = 300, initial_output: str = "",
    log: LogCallback = _quiet_log,
) -> tuple[bool, bool, str]:
    transcript = initial_output
    pending_log = ""
    deadline = time.monotonic() + timeout
    while True:
        # Drain buffered output even if the remote end has already closed the channel.
        while channel.recv_ready() and time.monotonic() < deadline:
            chunk = channel.recv(4096)
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")
            transcript += text
            pending_log += text
            while "\n" in pending_log:
                line, pending_log = pending_log.split("\n", 1)
                log(line)
        download_started = bool(re.search(r"download|transfer|\d+%", transcript, re.I))
        # Inspect the full transcript; failures take precedence over success messages.
        if _UPGRADE_FAILURE.search(transcript):
            return download_started, False, transcript
        if _UPGRADE_SUCCESS.search(transcript):
            return download_started, True, transcript
        if channel.closed or time.monotonic() >= deadline:
            return download_started, False, transcript
        time.sleep(0.2)


def safe_close_ssh_connection(ssh_client, channel) -> None:
    for connection in (channel, ssh_client):
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


class FirmwareHttpServer:
    """Expose only the selected firmware file on the selected Ethernet address."""

    def __init__(self, firmware_path: Path, port: int = 8000, host: str = "127.0.0.1"):
        self.firmware_path = Path(firmware_path).resolve()
        self.port = port
        self.host = host
        self._server = None
        self._thread = None

    def start(self) -> None:
        firmware_path = self.firmware_path

        class FirmwareHandler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(firmware_path.parent), **kwargs)

            def send_head(self):
                if unquote(urlsplit(self.path).path) != "/" + firmware_path.name:
                    self.send_error(404)
                    return None
                return super().send_head()

            def log_message(self, _format, *_args):
                # Windowed Windows applications have no sys.stderr.
                pass

        self._server = ThreadingHTTPServer((self.host, self.port), FirmwareHandler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="adtran-firmware-http", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


@dataclass
class AdtranDependencies:
    select_connection: Callable[..., NetworkAddress] = select_connection
    wait_for_ping: Callable[..., bool] = wait_for_ping
    ssh_connect_with_shell: Callable[..., tuple] = ssh_connect_with_shell
    execute_ssh_command: Callable[..., str] = execute_ssh_command
    monitor_upgrade_progress: Callable[..., tuple] = monitor_upgrade_progress
    safe_close_ssh_connection: Callable[..., None] = safe_close_ssh_connection
    sleep: Callable[[float], None] = time.sleep
    firmware_server_factory: Callable[..., object] = FirmwareHttpServer


def _value_after_equals(line: str) -> str:
    return line.split("=", 1)[1].strip().strip("'\"") if "=" in line else ""


def parse_wifi_config(output: Optional[str]) -> tuple[str, str]:
    values = {}
    for line in (output or "").splitlines():
        if "=" in line:
            values[line.split("=", 1)[0].strip()] = _value_after_equals(line)
    return values.get("wireless.i5g.ssid", ""), values.get("wireless.i5g.key", "")


def parse_mfg_info(output: Optional[str]) -> tuple[str, str]:
    values = {}
    for line in (output or "").splitlines():
        if "=" in line:
            values[line.split("=", 1)[0].strip()] = _value_after_equals(line)
    return values.get("MFG_SERIAL", ""), values.get("MFG_MAC", "")


def parse_build_info(output: Optional[str]) -> str:
    for line in (output or "").splitlines():
        if line.split("=", 1)[0].strip() == "DISTRIB_DESCRIPTION":
            return _value_after_equals(line)
    return ""


def _validate_csv_header(handle) -> None:
    handle.seek(0)
    header = next(csv.reader(handle), None)
    if header is not None and header != CSV_HEADERS:
        raise AdtranServiceError("The output file has different CSV columns. Choose a new CSV file.")
    handle.seek(0, 2)


def check_csv_output(csv_path: Union[str, Path]) -> Path:
    path = Path(csv_path).expanduser().resolve()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", newline="", encoding="utf-8-sig") as handle:
            _validate_csv_header(handle)
    except (OSError, UnicodeError) as exc:
        raise AdtranServiceError(
            "Cannot write the output CSV. Close it in Excel and choose a writable location."
        ) from exc
    return path


def append_device_record(record: DeviceRecord, csv_path: Union[str, Path]) -> Path:
    path = Path(csv_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", newline="", encoding="utf-8-sig") as handle:
        _validate_csv_header(handle)
        writer = csv.writer(handle)
        if handle.tell() == 0:
            writer.writerow(CSV_HEADERS)
        writer.writerow(record.to_csv_row())
    return path


def read_device_records(csv_path: Union[str, Path]) -> list[DeviceRecord]:
    path = Path(csv_path).expanduser()
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [DeviceRecord(*(row.get(column, "") for column in CSV_HEADERS))
                for row in csv.DictReader(handle)]


class AdtranUpgradeService:
    def __init__(self, dependencies=None, log=None, progress=None):
        self.dependencies = dependencies or AdtranDependencies()
        self._log = log or _quiet_log
        self._progress = progress or (lambda _percent, _message: None)
        self._secrets: set[str] = set()

    def redact(self, message: str) -> str:
        message = re.sub(
            r"(?im)(\b(?:wireless\.[\w.]+\.(?:key|password)|[\w.]*password)\s*=)[^\r\n]*",
            r"\1[redacted]", str(message),
        )
        for secret in sorted(self._secrets, key=len, reverse=True):
            if secret:
                message = message.replace(secret, "[redacted]")
        return message

    def log(self, message: str) -> None:
        self._log(self.redact(message))

    def progress(self, percent: int, message: str) -> None:
        self._progress(max(0, min(100, percent)), message)
        self.log(message)

    def run_job(self, job: AdtranJob) -> AdtranJobResult:
        self._secrets = {job.credentials.initial_password, job.credentials.upgraded_password}
        if job.operation not in {OPERATION_UPGRADE, OPERATION_INFO_ONLY}:
            raise AdtranServiceError(f"Unsupported ADTRAN operation: {job.operation}")
        for label, value in (("Device IP", job.device_ip), ("Local IP", job.local_ip),
                             ("Upgraded IP", job.upgraded_device_ip)):
            if label == "Local IP" and not value:
                continue
            if label == "Upgraded IP" and job.operation != OPERATION_UPGRADE:
                continue
            try:
                address = IPv4Address(value)
                if address.is_unspecified or address.is_multicast or address.is_loopback:
                    raise ValueError()
            except ValueError as exc:
                raise AdtranServiceError(f"{label} must be a valid device-network IPv4 address.") from exc
        if not job.credentials.candidates_for_ip(job.device_ip):
            raise AdtranServiceError("Missing SSH credentials. Open Settings and enter credentials.")
        if not job.output_csv_path.strip():
            raise AdtranServiceError("Choose an output CSV path.")
        if job.operation == OPERATION_UPGRADE:
            firmware = Path(job.firmware_path).expanduser()
            if not job.firmware_path or not firmware.is_file():
                raise AdtranServiceError(f"Firmware file not found: {firmware}")
            if firmware.stat().st_size == 0:
                raise AdtranServiceError("The firmware file is empty. Choose a valid image.")
            try:
                with firmware.open("rb") as handle:
                    handle.read(1)
            except OSError as exc:
                raise AdtranServiceError("The firmware file cannot be read.") from exc
        csv_path = str(check_csv_output(job.output_csv_path))
        self.progress(5, "Checking Ethernet connection...")
        try:
            connection = self.dependencies.select_connection(
                job.device_ip, job.interface_name, job.local_ip
            )
        except ValueError as exc:
            raise AdtranServiceError(str(exc)) from exc
        self.log(f"Using {connection.label}: {connection.ip}")
        if not self.dependencies.wait_for_ping(
            job.device_ip, timeout=60, interval=3, source_ip=connection.ip, log=self.log
        ):
            raise AdtranServiceError(
                f"Device {job.device_ip} did not respond. Check its power, Ethernet cable and IP."
            )
        if job.operation == OPERATION_INFO_ONLY:
            client, channel = self._connect_with_candidates(job.device_ip, job.credentials, connection)
            try:
                self.progress(70, "Reading device information...")
                record = self._extract_device_record(channel, job.device_ip, "Info Only")
            finally:
                self.dependencies.safe_close_ssh_connection(client, channel)
        else:
            record = self._run_upgrade(job, connection)
        self.progress(98, "Saving device information...")
        try:
            append_device_record(record, csv_path)
        except (OSError, UnicodeError, AdtranServiceError):
            return AdtranJobResult(False, record, csv_path, (
                "Device information was read but the CSV could not be saved. Close the file "
                "in Excel, then use Save Record to retry or choose another location. "
                "Do not repeat the firmware upgrade.",
            ))
        self.progress(100, "Device complete.")
        return AdtranJobResult(True, record, csv_path)

    def _command(self, channel, command, **kwargs) -> str:
        return self.dependencies.execute_ssh_command(channel, command, log=self.log, **kwargs) or ""

    def _confirm_if_needed(self, channel, output: str) -> str:
        if re.search(r"confirm|proceed|y/n|yes/no", output, re.I):
            self.log("Confirming device command.")
            return output + "\n" + self._command(channel, "y")
        return output

    def _run_upgrade(self, job, connection) -> DeviceRecord:
        firmware_path = Path(job.firmware_path).expanduser().resolve()
        server = self.dependencies.firmware_server_factory(
            firmware_path, job.http_port, host=connection.ip
        )
        client = channel = None
        try:
            self.progress(10, "Starting firmware transfer server...")
            try:
                server.start()
            except OSError as exc:
                raise AdtranServiceError(
                    "Cannot start the firmware server. Close other copies of the upgrader "
                    "and check that the selected Ethernet address is still connected."
                ) from exc
            self.log(f"Firmware server: {connection.ip}:{server.port}")
            self.progress(25, "Connecting to ADTRAN device...")
            client, channel = self._connect_with_candidates(job.device_ip, job.credentials, connection)
            self.progress(35, "Preparing device settings for upgrade...")
            output = self._confirm_if_needed(channel, self._command(channel, "system restore nvram"))
            if _UPGRADE_FAILURE.search(output):
                raise AdtranServiceError("The device rejected the preparation command. Upgrade stopped.")
            url = f"http://{connection.ip}:{server.port}/{quote(firmware_path.name)}"
            self.progress(48, "Starting firmware upgrade...")
            output = self._confirm_if_needed(channel, self._command(channel, f"upgrade {url}"))
            self.progress(58, "Waiting for confirmed firmware completion...")
            _started, complete, transcript = self.dependencies.monitor_upgrade_progress(
                channel, initial_output=output, log=self.log
            )
            self.log(transcript)
            if not complete:
                raise AdtranServiceError(
                    "Firmware completion was not confirmed. Defaults were not restored and "
                    "no completed-device row was recorded. Keep the device powered and check "
                    "its status before retrying. For a download failure, check Windows "
                    "Firewall access on the Ethernet network and the selected firmware."
                )
            self.progress(68, "Restoring device defaults...")
            output = self._confirm_if_needed(channel, self._command(channel, "system restore default"))
            if _UPGRADE_FAILURE.search(output):
                raise AdtranServiceError("The device rejected the restore-defaults command.")
            self.dependencies.safe_close_ssh_connection(client, channel)
            client = channel = None
            # The download is over; do not leave a listening server open during reboot.
            server.stop()
            self.progress(80, f"Waiting for reboot at {job.upgraded_device_ip}...")
            self.dependencies.sleep(30)
            client, channel = self._connect_with_candidates(
                job.upgraded_device_ip, job.credentials, connection,
                max_attempts=30, refresh_address=True,
            )
            self.progress(94, "Reading upgraded device information...")
            return self._extract_device_record(channel, job.upgraded_device_ip, "Upgrade")
        finally:
            self.dependencies.safe_close_ssh_connection(client, channel)
            server.stop()

    def _connect_with_candidates(
        self, ip, credentials, connection, max_attempts=5, refresh_address=False,
    ) -> tuple[object, object]:
        candidates = credentials.candidates_for_ip(ip)
        last_error = "The device is not accepting SSH connections."
        for attempt in range(max_attempts):
            try:
                current = self.dependencies.select_connection(
                    ip, connection.name, "" if refresh_address else connection.ip
                )
            except ValueError as exc:
                current = None
                last_error = str(exc)
            if current is not None:
                self.log(f"SSH attempt {attempt + 1}/{max_attempts}, source {current.ip}")
                errors = []
                for username, password, label in candidates:
                    client, channel, error = self.dependencies.ssh_connect_with_shell(
                        ip, username=username, password=password, source_ip=current.ip, log=self.log
                    )
                    if client is not None and channel is not None:
                        self.log(f"Connected using {label} credentials.")
                        return client, channel
                    self.dependencies.safe_close_ssh_connection(client, channel)
                    errors.append(error)
                    if error != "auth_failed":
                        break
                if errors and all(error == "auth_failed" for error in errors):
                    raise AuthenticationError(
                        "Authentication failed. Open Settings and check the initial and "
                        "upgraded SSH credentials."
                    )
            if attempt + 1 < max_attempts:
                self.dependencies.sleep(10)
        raise AdtranServiceError(f"Failed to connect to {ip} over SSH. {last_error}")

    def _extract_device_record(self, channel, device_ip, operation_type) -> DeviceRecord:
        # Wi-Fi output is sensitive even before its password has been parsed for redaction.
        ssid, wifi_key = parse_wifi_config(self._command(channel, "show wifi config", log_output=False))
        self._secrets.add(wifi_key)
        serial, mac = parse_mfg_info(self._command(channel, "show mfg"))
        version = parse_build_info(self._command(channel, "show buildinfo"))
        if not serial or not mac or not version:
            raise AdtranServiceError(
                "Device information is incomplete (serial, MAC or firmware version missing). "
                "No completed row was recorded. Use Record device info only to retry extraction."
            )
        return DeviceRecord(
            time.strftime("%Y-%m-%d %H:%M:%S"), device_ip, ssid, wifi_key,
            serial, mac, version, operation_type,
        )
