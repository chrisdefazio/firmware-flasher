"""GUI-safe ADTRAN workflow services.

The existing CLI scripts remain the source of low-level SSH behavior. This
module wraps that behavior in small, testable objects that can report progress
to a desktop UI instead of prompting in a terminal.
"""

from __future__ import annotations

import contextlib
import csv
import io
import platform
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Iterable, Optional, Union
from urllib.parse import quote

import paramiko
from paramiko.ssh_exception import NoValidConnectionsError

from network_utils import (
    get_gateway_for_connection,
    get_wired_interface_ip,
    wait_for_ping,
)


CSV_HEADERS = [
    "Timestamp",
    "Device IP",
    "WiFi SSID",
    "WiFi Password",
    "Serial Number",
    "MAC Address",
    "Firmware Version",
    "Operation Type",
]

OPERATION_UPGRADE = "upgrade"
OPERATION_INFO_ONLY = "info_only"

LogCallback = Callable[[str], None]
ProgressCallback = Callable[[int, str], None]


class AdtranServiceError(RuntimeError):
    """Raised when an ADTRAN workflow cannot complete."""


@dataclass(frozen=True)
class AdtranCredentials:
    initial_username: str = ""
    initial_password: str = ""
    upgraded_username: str = ""
    upgraded_password: str = ""

    def candidates_for_ip(self, ip: str) -> list[tuple[str, str, str]]:
        """Return credential candidates in the same order as the CLI."""
        initial = (self.initial_username, self.initial_password, "initial")
        upgraded = (self.upgraded_username, self.upgraded_password, "upgraded")
        ordered = [upgraded, initial] if ip.startswith("172.16.192.") else [initial, upgraded]

        candidates: list[tuple[str, str, str]] = []
        seen: set[tuple[str, str]] = set()
        for username, password, label in ordered:
            if not username or not password:
                continue
            key = (username, password)
            if key in seen:
                continue
            candidates.append((username, password, label))
            seen.add(key)
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
    wifi_password: str
    serial_number: str
    mac_address: str
    firmware_version: str
    operation_type: str

    def to_csv_row(self) -> list[str]:
        return [
            self.timestamp,
            self.device_ip,
            self.ssid,
            self.wifi_password,
            self.serial_number,
            self.mac_address,
            self.firmware_version,
            self.operation_type,
        ]


@dataclass(frozen=True)
class AdtranJobResult:
    success: bool
    record: Optional[DeviceRecord] = None
    csv_path: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)


def clear_ssh_key(ip: str) -> None:
    """Remove a stale known-host key for the device IP when supported."""
    if platform.system() == "Windows":
        print(f"On Windows, remove {ip} from the SSH known_hosts file if needed.")
        return
    try:
        subprocess.run(
            ["ssh-keygen", "-R", ip],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
        print(f"Cleared SSH key for {ip}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Could not clear SSH key for {ip}: {exc}")


def test_ssh_port_reachability(
    ip: str,
    port: int = 22,
    timeout: int = 5,
    source_ip: Optional[str] = None,
) -> tuple[bool, str]:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        if source_ip:
            sock.bind((source_ip, 0))
        sock.connect((ip, port))
        with sock:
            local_host, local_port = sock.getsockname()
            return True, f"local {local_host}:{local_port} -> {ip}:{port}"
    except socket.timeout:
        return False, f"tcp_timeout: unable to reach {ip}:{port} within {timeout}s"
    except ConnectionRefusedError:
        return False, f"tcp_refused: {ip}:{port} refused the connection"
    except OSError as err:
        errno_text = f" errno={err.errno}" if getattr(err, "errno", None) is not None else ""
        return False, f"tcp_os_error:{errno_text} {err}"


def _make_bound_socket(ip: str, source_ip: str, timeout: int) -> Optional[socket.socket]:
    try:
        bound_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        bound_sock.bind((source_ip, 0))
        bound_sock.settimeout(timeout)
        bound_sock.connect((ip, 22))
        print(f"Using bound source socket for Paramiko: {source_ip}")
        return bound_sock
    except OSError as exc:
        print(f"Bound source socket setup failed for {source_ip}: {exc}")
        try:
            bound_sock.close()
        except Exception:
            pass
        return None


def _open_interactive_shell(client: paramiko.SSHClient, timeout: int):
    channel = client.invoke_shell()
    channel.settimeout(timeout)
    time.sleep(2)
    initial_output = b""
    while channel.recv_ready():
        initial_output += channel.recv(4096)
    initial_str = initial_output.decode("utf-8", errors="ignore")
    if initial_str.strip():
        print("----- Initial SSH Connection Output -----")
        print(initial_str)
        print("-----------------------------------------")
    return channel


def ssh_connect_with_shell(
    ip: str,
    username: Optional[str] = None,
    password: Optional[str] = None,
    timeout: int = 10,
    prompt_on_fail: bool = False,
    source_ip: Optional[str] = None,
) -> tuple[Optional[object], Optional[object], Optional[str]]:
    """Connect to an ADTRAN device over SSH and open an interactive shell."""
    del prompt_on_fail
    print(f"Connecting to {ip} via SSH...")
    if not username or not password:
        print("Provided SSH credentials are incomplete.")
        return None, None, "missing_credentials"

    if source_ip:
        print(f"Binding SSH connection source to local IP: {source_ip}")
    can_reach, message = test_ssh_port_reachability(
        ip,
        timeout=max(3, min(timeout, 10)),
        source_ip=source_ip,
    )
    if can_reach:
        print(f"TCP preflight succeeded: {message}")
    else:
        print(f"TCP preflight failed before Paramiko auth: {message}")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        connect_kwargs = {
            "username": username,
            "password": password,
            "timeout": timeout,
            "banner_timeout": timeout,
            "auth_timeout": timeout,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if source_ip:
            bound_sock = _make_bound_socket(ip, source_ip, timeout)
            if bound_sock is not None:
                connect_kwargs["sock"] = bound_sock

        client.connect(ip, **connect_kwargs)
        print(f"Successfully connected to {ip} with password auth.")
        return client, _open_interactive_shell(client, timeout), "connected"
    except paramiko.AuthenticationException as auth_err:
        print(f"Password authentication failed for '{username}': {auth_err}")
        try:
            client.close()
        except Exception:
            pass
        return _try_keyboard_interactive(ip, username, password, timeout, source_ip)
    except NoValidConnectionsError as conn_err:
        print(f"Socket connection failed for {ip}:22 ({conn_err})")
        return None, None, "no_valid_connections"
    except paramiko.SSHException as ssh_err:
        print(f"SSH error connecting to {ip}: {ssh_err}")
        return None, None, "ssh_error"
    except socket.timeout:
        print(f"Connection to {ip} timed out after {timeout} seconds")
        return None, None, "timeout"
    except socket.error as sock_err:
        print(f"Socket error connecting to {ip}: {sock_err}")
        return None, None, "socket_error"
    except Exception as exc:
        print(f"Unexpected error connecting to {ip}: {type(exc).__name__}: {exc}")
        return None, None, "unexpected_error"


def _try_keyboard_interactive(
    ip: str,
    username: str,
    password: str,
    timeout: int,
    source_ip: Optional[str],
) -> tuple[Optional[object], Optional[object], Optional[str]]:
    def handler(_title, _instructions, prompt_list):
        return [password for _prompt, _echo in prompt_list]

    transport = None
    try:
        if source_ip:
            bound_sock = _make_bound_socket(ip, source_ip, timeout)
            transport = paramiko.Transport(bound_sock) if bound_sock else paramiko.Transport((ip, 22))
        else:
            transport = paramiko.Transport((ip, 22))
        transport.start_client(timeout=timeout)
        transport.auth_interactive(username, handler)
        if not transport.is_authenticated():
            raise paramiko.AuthenticationException("keyboard-interactive auth failed")

        client = paramiko.SSHClient()
        client._transport = transport
        print(f"Successfully connected to {ip} with keyboard-interactive auth.")
        return client, _open_interactive_shell(client, timeout), "connected"
    except paramiko.AuthenticationException as exc:
        print(f"Keyboard-interactive authentication failed: {exc}")
        if transport is not None:
            transport.close()
        return None, None, "auth_failed"
    except Exception as exc:
        print(f"Keyboard-interactive authentication also failed: {exc}")
        if transport is not None:
            transport.close()
        return None, None, "auth_failed"


def execute_ssh_command(
    channel: object,
    command: str,
    wait_time: int = 5,
    max_output_wait: int = 30,
) -> Optional[str]:
    if not channel:
        return None
    while channel.recv_ready():
        channel.recv(4096)

    print(f">>> Executing command: {command}")
    channel.send(command + "\n")

    output = b""
    start_time = time.time()
    last_receive_time = start_time
    time.sleep(1)

    while (
        time.time() - last_receive_time < wait_time
        and time.time() - start_time < max_output_wait
    ):
        if channel.recv_ready():
            chunk = channel.recv(4096)
            output += chunk
            print(chunk.decode("utf-8", errors="ignore"), end="")
            sys.stdout.flush()
            last_receive_time = time.time()
        else:
            time.sleep(0.1)

    duration = time.time() - start_time
    print(f">>> Command completed in {duration:.2f} seconds")
    return output.decode("utf-8", errors="ignore")


def monitor_upgrade_progress(channel: object, timeout: int = 300) -> tuple[bool, bool, str]:
    print("----- Monitoring Upgrade Progress -----")
    start_time = time.time()
    last_output = ""
    download_started = False
    upgrade_complete = False

    while time.time() - start_time < timeout:
        if channel.recv_ready():
            output = channel.recv(4096).decode("utf-8", errors="ignore")
            print(output, end="")
            sys.stdout.flush()
            last_output += output

            lower = output.lower()
            if "download" in lower or "transfer" in lower or "%" in output:
                download_started = True
            if "success" in lower or "complete" in lower:
                upgrade_complete = True
                print("Upgrade completed successfully.")
                break
            if "error" in lower or "fail" in lower:
                print("Upgrade failed.")
                break
        else:
            time.sleep(0.5)

    if time.time() - start_time >= timeout:
        print("Monitoring timed out.")
    print("----- End of Monitoring -----")
    return download_started, upgrade_complete, last_output


def safe_close_ssh_connection(ssh_client: Optional[object], channel: Optional[object]) -> None:
    if channel:
        try:
            channel.close()
        except Exception:
            pass
    if ssh_client:
        try:
            ssh_client.close()
        except Exception:
            pass
    time.sleep(2)


@dataclass
class AdtranDependencies:
    get_wired_interface_ip: Callable[[], Optional[tuple[str, str]]] = get_wired_interface_ip
    get_gateway_for_connection: Callable[[Optional[str], Optional[str]], Optional[str]] = (
        get_gateway_for_connection
    )
    wait_for_ping: Callable[..., bool] = wait_for_ping
    clear_ssh_key: Callable[[str], None] = clear_ssh_key
    ssh_connect_with_shell: Callable[..., tuple[Optional[object], Optional[object], Optional[str]]] = (
        ssh_connect_with_shell
    )
    execute_ssh_command: Callable[..., Optional[str]] = execute_ssh_command
    monitor_upgrade_progress: Callable[..., tuple[bool, bool, str]] = (
        monitor_upgrade_progress
    )
    safe_close_ssh_connection: Callable[[Optional[object], Optional[object]], None] = (
        safe_close_ssh_connection
    )
    sleep: Callable[[float], None] = time.sleep
    firmware_server_factory: Optional[Callable[[Path, int], object]] = None


class _CallbackTextIO(io.TextIOBase):
    def __init__(self, callback: LogCallback):
        super().__init__()
        self._callback = callback
        self._buffer = ""

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        if not text:
            return 0
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self._callback(line.rstrip())
        return len(text)

    def flush(self) -> None:
        if self._buffer.strip():
            self._callback(self._buffer.rstrip())
        self._buffer = ""


@contextlib.contextmanager
def _redirect_legacy_output(log: LogCallback):
    stdout = _CallbackTextIO(log)
    stderr = _CallbackTextIO(log)
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            yield
        finally:
            stdout.flush()
            stderr.flush()


class FirmwareHttpServer:
    """Small threaded HTTP server for serving one firmware directory."""

    def __init__(self, directory: Path, port: int = 8000, host: str = ""):
        self.directory = Path(directory)
        self.port = port
        self.host = host
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._server is not None:
            return
        handler = partial(SimpleHTTPRequestHandler, directory=str(self.directory))
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="adtran-firmware-http",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


def _value_after_equals(line: str) -> str:
    if "=" not in line:
        return ""
    return line.split("=", 1)[1].strip().strip("'\"")


def parse_wifi_config(output: Optional[str]) -> tuple[str, str]:
    ssid = ""
    wifi_key = ""
    for line in (output or "").splitlines():
        if "wireless.i5g.ssid" in line:
            ssid = _value_after_equals(line)
        elif "wireless.i5g.key" in line:
            wifi_key = _value_after_equals(line)
    return ssid, wifi_key


def parse_mfg_info(output: Optional[str]) -> tuple[str, str]:
    serial = ""
    mac = ""
    for line in (output or "").splitlines():
        if "MFG_SERIAL" in line:
            serial = _value_after_equals(line)
        elif "MFG_MAC" in line:
            mac = _value_after_equals(line)
    return serial, mac


def parse_build_info(output: Optional[str]) -> str:
    for line in (output or "").splitlines():
        if "DISTRIB_DESCRIPTION" in line:
            return _value_after_equals(line)
    return ""


def append_device_record(record: DeviceRecord, csv_path: Union[str, Path]) -> Path:
    path = Path(csv_path).expanduser()
    if path.parent and str(path.parent) != ".":
        path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = path.exists()
    with path.open("a", newline="") as handle:
        writer = csv.writer(handle)
        if not file_exists:
            writer.writerow(CSV_HEADERS)
        writer.writerow(record.to_csv_row())
    return path


def read_device_records(csv_path: Union[str, Path]) -> list[DeviceRecord]:
    path = Path(csv_path).expanduser()
    if not path.exists():
        return []
    records: list[DeviceRecord] = []
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            records.append(
                DeviceRecord(
                    timestamp=row.get("Timestamp", ""),
                    device_ip=row.get("Device IP", ""),
                    ssid=row.get("WiFi SSID", ""),
                    wifi_password=row.get("WiFi Password", ""),
                    serial_number=row.get("Serial Number", ""),
                    mac_address=row.get("MAC Address", ""),
                    firmware_version=row.get("Firmware Version", ""),
                    operation_type=row.get("Operation Type", ""),
                )
            )
    return records


class AdtranUpgradeService:
    def __init__(
        self,
        dependencies: Optional[AdtranDependencies] = None,
        log: Optional[LogCallback] = None,
        progress: Optional[ProgressCallback] = None,
    ):
        self.dependencies = dependencies or AdtranDependencies()
        self._log = log or (lambda _message: None)
        self._progress = progress or (lambda _percent, _message: None)

    def run_job(self, job: AdtranJob) -> AdtranJobResult:
        with _redirect_legacy_output(self.log):
            return self._run_job(job)

    def log(self, message: str) -> None:
        self._log(message)

    def progress(self, percent: int, message: str) -> None:
        self._progress(max(0, min(100, percent)), message)
        self.log(message)

    def _run_job(self, job: AdtranJob) -> AdtranJobResult:
        if job.operation not in {OPERATION_UPGRADE, OPERATION_INFO_ONLY}:
            raise AdtranServiceError(f"Unsupported ADTRAN operation: {job.operation}")

        local_ip, interface_name = self._resolve_local_connection(job)
        device_ip = self._resolve_device_ip(job, interface_name, local_ip)
        csv_path = str(Path(job.output_csv_path).expanduser())

        if job.operation == OPERATION_INFO_ONLY:
            record = self._run_info_only(job, device_ip, local_ip)
            append_device_record(record, csv_path)
            return AdtranJobResult(success=True, record=record, csv_path=csv_path)

        record, warnings = self._run_upgrade(job, device_ip, local_ip)
        append_device_record(record, csv_path)
        return AdtranJobResult(
            success=True,
            record=record,
            csv_path=csv_path,
            warnings=tuple(warnings),
        )

    def _resolve_local_connection(self, job: AdtranJob) -> tuple[str, str]:
        if job.local_ip.strip():
            return job.local_ip.strip(), job.interface_name.strip()

        self.progress(5, "Detecting Ethernet connection...")
        detected = self.dependencies.get_wired_interface_ip()
        if not detected:
            raise AdtranServiceError(
                "No wired Ethernet connection was detected. Select an interface manually."
            )
        interface_name, local_ip = detected
        self.log(f"Detected Ethernet interface: {interface_name} -> {local_ip}")
        return local_ip, interface_name

    def _resolve_device_ip(self, job: AdtranJob, interface_name: str, local_ip: str) -> str:
        if job.device_ip.strip():
            return job.device_ip.strip()
        gateway = self.dependencies.get_gateway_for_connection(interface_name, local_ip)
        if gateway:
            self.log(f"Detected gateway address: {gateway}")
            return gateway
        return "192.168.1.1"

    def _run_info_only(self, job: AdtranJob, device_ip: str, local_ip: str) -> DeviceRecord:
        self.progress(10, f"Preparing device connection at {device_ip}...")
        self.dependencies.clear_ssh_key(device_ip)
        self.dependencies.wait_for_ping(device_ip, timeout=60, interval=3)

        self.progress(25, "Connecting over SSH...")
        ssh_client, channel = self._connect_with_candidates(
            device_ip,
            job.credentials,
            source_ip=local_ip,
            max_attempts=5,
            retry_delay=15,
        )
        try:
            self.progress(70, "Extracting device information...")
            return self._extract_device_record(channel, device_ip, "Info Only")
        finally:
            self.dependencies.safe_close_ssh_connection(ssh_client, channel)

    def _run_upgrade(
        self,
        job: AdtranJob,
        device_ip: str,
        local_ip: str,
    ) -> tuple[DeviceRecord, list[str]]:
        firmware_path = Path(job.firmware_path).expanduser()
        if not firmware_path.exists() or not firmware_path.is_file():
            raise AdtranServiceError(f"Firmware file not found: {firmware_path}")

        warnings: list[str] = []
        server_factory = self.dependencies.firmware_server_factory or FirmwareHttpServer
        server = server_factory(firmware_path.parent, job.http_port)
        ssh_client = None
        channel = None

        try:
            self.progress(8, "Starting local firmware server...")
            server.start()
            self.log(f"HTTP firmware server listening on port {server.port}")

            self.progress(15, f"Waiting for device network at {device_ip}...")
            self.dependencies.clear_ssh_key(device_ip)
            self.dependencies.wait_for_ping(device_ip, timeout=60, interval=3)
            self.dependencies.sleep(5)

            self.progress(25, "Connecting to ADTRAN device...")
            ssh_client, channel = self._connect_with_candidates(
                device_ip,
                job.credentials,
                source_ip=local_ip,
                max_attempts=5,
                retry_delay=10,
            )

            self.progress(35, "Clearing device RAM before upgrade...")
            output = self.dependencies.execute_ssh_command(channel, "system restore nvram")
            self._confirm_if_needed(channel, output)

            firmware_name = quote(firmware_path.name)
            upgrade_url = f"http://{local_ip}:{server.port}/{firmware_name}"
            self.progress(48, "Starting firmware upgrade...")
            output = self.dependencies.execute_ssh_command(channel, f"upgrade {upgrade_url}")
            self._confirm_if_needed(channel, output)

            self.progress(58, "Monitoring firmware upgrade...")
            download_started, upgrade_complete, _last_output = (
                self.dependencies.monitor_upgrade_progress(channel)
            )
            if not download_started:
                warnings.append("The device did not clearly report that the download started.")
            if not upgrade_complete:
                warnings.append("The device did not clearly report that the upgrade completed.")

            self.progress(68, "Restoring device defaults...")
            output = self.dependencies.execute_ssh_command(channel, "system restore default")
            self._confirm_if_needed(channel, output)

            self.progress(74, "Closing pre-reboot SSH session...")
            self.dependencies.safe_close_ssh_connection(ssh_client, channel)
            ssh_client = None
            channel = None

            upgraded_ip = job.upgraded_device_ip.strip() or "172.16.192.1"
            self.progress(80, f"Waiting for upgraded device at {upgraded_ip}...")
            if not self.dependencies.wait_for_ping(upgraded_ip, timeout=300, interval=5):
                raise AdtranServiceError(
                    f"Unable to reach upgraded device at {upgraded_ip}."
                )
            self.dependencies.sleep(30)
            self.dependencies.clear_ssh_key(upgraded_ip)

            self.progress(88, "Connecting to upgraded device...")
            ssh_client, channel = self._connect_with_candidates(
                upgraded_ip,
                job.credentials,
                source_ip=local_ip,
                max_attempts=10,
                retry_delay=10,
            )

            self.progress(94, "Recording upgraded device information...")
            record = self._extract_device_record(channel, upgraded_ip, "Upgrade")
            self.progress(100, "ADTRAN device complete.")
            return record, warnings
        finally:
            if ssh_client is not None or channel is not None:
                self.dependencies.safe_close_ssh_connection(ssh_client, channel)
            server.stop()

    def _confirm_if_needed(self, channel: object, output: Optional[str]) -> None:
        lower = (output or "").lower()
        if any(token in lower for token in ("confirm", "proceed", "y/n")):
            self.log("Confirmation prompt detected. Sending yes.")
            self.dependencies.execute_ssh_command(channel, "y")

    def _connect_with_candidates(
        self,
        ip: str,
        credentials: AdtranCredentials,
        source_ip: str,
        max_attempts: int,
        retry_delay: int,
    ) -> tuple[object, object]:
        candidates = credentials.candidates_for_ip(ip)
        if not candidates:
            raise AdtranServiceError(
                "Missing SSH credentials. Open Settings and enter initial/upgraded credentials."
            )

        for attempt in range(1, max_attempts + 1):
            self.log(f"SSH attempt {attempt}/{max_attempts} to {ip}")
            for username, password, label in candidates:
                self.log(f"Trying {label} credentials with username '{username}'")
                ssh_client, channel, error = self.dependencies.ssh_connect_with_shell(
                    ip,
                    username=username,
                    password=password,
                    prompt_on_fail=False,
                    source_ip=source_ip or None,
                )
                if ssh_client and channel:
                    self.log(f"Connected to {ip} using {label} credentials.")
                    return ssh_client, channel
                if error == "missing_credentials":
                    raise AdtranServiceError(
                        "Missing SSH credentials. Open Settings and enter credentials."
                    )

            if attempt < max_attempts:
                self.log(f"SSH connection failed. Retrying in {retry_delay} seconds...")
                self.dependencies.sleep(retry_delay)

        raise AdtranServiceError(f"Failed to connect to {ip} over SSH.")

    def _extract_device_record(
        self,
        channel: object,
        device_ip: str,
        operation_type: str,
    ) -> DeviceRecord:
        wifi_output = self.dependencies.execute_ssh_command(channel, "show wifi config")
        ssid, wifi_key = parse_wifi_config(wifi_output)

        mfg_output = self.dependencies.execute_ssh_command(channel, "show mfg")
        serial, mac = parse_mfg_info(mfg_output)

        build_output = self.dependencies.execute_ssh_command(channel, "show buildinfo")
        firmware_version = parse_build_info(build_output)

        return DeviceRecord(
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            device_ip=device_ip,
            ssid=ssid,
            wifi_password=wifi_key,
            serial_number=serial,
            mac_address=mac,
            firmware_version=firmware_version,
            operation_type=operation_type,
        )


def mask_secret(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 2:
        return "*" * len(secret)
    return f"{secret[:2]}{'*' * (len(secret) - 2)}"


def summarize_records(records: Iterable[DeviceRecord]) -> list[list[str]]:
    return [record.to_csv_row() for record in records]
