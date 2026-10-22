"""Shared network and connection detection for firmware upgraders.
No UI or TerminalMenu; device-specific scripts handle prompts."""
from ipaddress import IPv4Address
import platform
import select
import subprocess
import time


def drain_tty_input():
    """Drain any leftover bytes from the terminal after a menu closes.
    Some terminals send \\r\\n for Enter; the menu consumes \\r and the next menu
    reads the leftover \\n (e.g. as 'move down'), causing a double-enter feel.
    Call this after each TerminalMenu.show() to avoid that.
    Uses a short timeout and chunk reads to avoid adding noticeable lag."""
    if platform.system() == "Windows":
        return
    try:
        tty = open("/dev/tty", "rb")
        try:
            tty_fd = tty.fileno()
            deadline = time.time() + 0.15  # drain at most ~150ms so we don't add lag
            while time.time() < deadline:
                r, _, _ = select.select([tty_fd], [], [], 0.01)  # 10ms timeout when empty
                if not r:
                    break
                chunk = tty.read(256)
                if not chunk:
                    break
        finally:
            tty.close()
    except (OSError, AttributeError):
        pass


def reset_tty_sane():
    """Reset the terminal to sane defaults (cooked mode, echo on, etc.).
    Call after returning from code that may have left the terminal in a bad
    state (e.g. SSH session, subprocess, or heavy output)."""
    if platform.system() == "Windows":
        return
    try:
        subprocess.run(
            ["stty", "sane"],
            capture_output=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def get_os_type():
    """Return the current OS for platform-specific logic: Darwin, Linux, or Windows."""
    return platform.system()


def _get_wired_interface_ip_darwin():
    """Detect a wired (Ethernet/USB LAN) interface with IPv4 on macOS. Returns (iface_name, ip) or None."""
    try:
        output = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True, text=True, timeout=5
        )
        if output.returncode != 0:
            return None
        lines = output.stdout.splitlines()
        i = 0
        devices_to_try = []
        while i < len(lines):
            line = lines[i]
            if "Hardware Port:" in line:
                port_name = line.split("Hardware Port:")[-1].strip()
                if any(x in port_name for x in ("Ethernet", "LAN", "Thunderbolt")) and "Wi-Fi" not in port_name and "Bluetooth" not in port_name:
                    i += 1
                    while i < len(lines) and lines[i].strip().startswith("Device:"):
                        dev_line = lines[i]
                        dev = dev_line.split("Device:")[-1].strip()
                        if dev:
                            devices_to_try.append((port_name, dev))
                        i += 1
                    continue
            i += 1
        for port_name, dev in devices_to_try:
            ip_out = subprocess.run(
                ["ipconfig", "getifaddr", dev],
                capture_output=True, text=True, timeout=2
            )
            if ip_out.returncode == 0 and ip_out.stdout.strip():
                ip = ip_out.stdout.strip()
                if ip and not ip.startswith("127."):
                    return (port_name, ip)
        out = subprocess.run(
            "ifconfig | grep -A 2 '^en' | grep 'inet '",
            shell=True, capture_output=True, text=True, timeout=5
        )
        if out.returncode == 0 and out.stdout.strip():
            for line in out.stdout.strip().splitlines():
                parts = line.strip().split()
                if len(parts) >= 2 and parts[0] == "inet":
                    ip = parts[1]
                    if not ip.startswith("127."):
                        return ("enX", ip)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def _get_wired_interface_ip_linux():
    """Detect a wired interface with IPv4 on Linux. Returns (iface_name, ip) or None."""
    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "addr", "show"],
            capture_output=True, text=True, timeout=5
        )
        if out.returncode != 0:
            return None
        for line in out.stdout.splitlines():
            if "lo" in line.split()[1]:
                continue
            parts = line.split()
            if len(parts) >= 4 and parts[2] == "inet":
                iface = parts[1].rstrip(":")
                ip = parts[3].split("/")[0]
                if not ip.startswith("127."):
                    return (iface, ip)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    try:
        out = subprocess.run(
            "ifconfig | grep -E '^[a-z]' | grep -v lo",
            shell=True, capture_output=True, text=True, timeout=5
        )
        if out.returncode == 0 and out.stdout.strip():
            blocks = out.stdout.strip().split("\n\n")
            for block in blocks:
                lines = block.split("\n")
                iface = lines[0].split(":")[0].strip()
                for line in lines[1:]:
                    if "inet " in line and "127." not in line:
                        parts = line.strip().split()
                        for i, p in enumerate(parts):
                            if p == "inet" and i + 1 < len(parts):
                                ip = parts[i + 1]
                                if "/" in ip:
                                    ip = ip.split("/")[0]
                                return (iface, ip)
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _get_wired_interface_ip_windows():
    """Detect a wired Ethernet adapter with IPv4 on Windows. Returns (adapter_name, ip) or None."""
    try:
        output = subprocess.run(
            "ipconfig",
            shell=True, capture_output=True, text=True, timeout=10
        )
        if output.returncode != 0:
            return None
        lines = (output.stdout or "").splitlines()
        current_adapter = None
        for line in lines:
            if line.strip() and ":" in line and not line.strip().startswith(" "):
                current_adapter = line.split(":")[0].strip()
            if current_adapter and "IPv4 Address" in line:
                if any(x in current_adapter for x in ("Virtual", "VPN", "Bluetooth", "Loopback")):
                    continue
                if any(x in current_adapter for x in ("Ethernet", "LAN", "Local Area")):
                    ip = line.split(":")[-1].strip()
                    if ip and not ip.startswith("127."):
                        return (current_adapter, ip)
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def get_wired_interface_ip():
    """Return (iface_name, ip) for a wired interface with IPv4 on current OS, or None."""
    os_type = get_os_type()
    if os_type == "Darwin":
        return _get_wired_interface_ip_darwin()
    if os_type == "Linux":
        return _get_wired_interface_ip_linux()
    if os_type == "Windows":
        return _get_wired_interface_ip_windows()
    return None


def wait_for_ethernet_connection(timeout=300, interval=3):
    """Poll for a wired Ethernet interface with IPv4. Allows switching to manual mode during wait.
    Returns (iface_name, ip) when detected, or None on timeout or if user switches to manual."""
    def manual_switch_requested(tty_file):
        """Non-blocking check for Enter key to switch to manual mode."""
        if tty_file is None:
            return False
        try:
            while True:
                r, _, _ = select.select([tty_file.fileno()], [], [], 0)
                if not r:
                    return False
                chunk = tty_file.read(256)
                if not chunk:
                    return False
                if b"\n" in chunk or b"\r" in chunk:
                    return True
        except (OSError, ValueError):
            return False

    tty_file = None
    if platform.system() != "Windows":
        try:
            tty_file = open("/dev/tty", "rb")
        except OSError:
            tty_file = None

    start_time = time.time()
    last_status = 0
    try:
        while time.time() - start_time < timeout:
            if manual_switch_requested(tty_file):
                return None
            result = get_wired_interface_ip()
            if result:
                return result
            elapsed = int(time.time() - start_time)
            if elapsed >= last_status + interval or last_status == 0:
                print(f"Waiting for Ethernet connection... ({elapsed}s)")
                last_status = elapsed
            time.sleep(interval)
    finally:
        if tty_file is not None:
            try:
                tty_file.close()
            except OSError:
                pass
    return None


def get_gateway_for_connection(iface_name=None, computer_ip=None):
    """Get the default gateway (router) for the given interface or computer IP.
    Returns gateway IP string or None."""
    os_type = get_os_type()
    if os_type == "Darwin":
        if iface_name:
            if " " in iface_name or "LAN" in iface_name or "Ethernet" in iface_name:
                try:
                    out = subprocess.run(
                        ["networksetup", "-getinfo", iface_name],
                        capture_output=True, text=True, timeout=5
                    )
                    if out.returncode == 0:
                        for line in out.stdout.splitlines():
                            if line.strip().startswith("Router:"):
                                gw = line.split("Router:")[-1].strip()
                                if gw and gw != "(none)":
                                    return gw
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                    pass
            try:
                out = subprocess.run(
                    "netstat -nr -f inet",
                    shell=True, capture_output=True, text=True, timeout=5
                )
                if out.returncode == 0:
                    for line in out.stdout.splitlines():
                        parts = line.split()
                        if len(parts) >= 2 and parts[0] == "default" and len(parts) >= 6 and parts[-1] == iface_name:
                            return parts[1]
            except (subprocess.TimeoutExpired, OSError):
                pass
        if computer_ip:
            try:
                out = subprocess.run(
                    "netstat -nr -f inet",
                    shell=True, capture_output=True, text=True, timeout=5
                )
                if out.returncode == 0:
                    for line in out.stdout.splitlines():
                        parts = line.split()
                        if len(parts) >= 2 and parts[0] == "default":
                            return parts[1]
            except (subprocess.TimeoutExpired, OSError):
                pass
    if os_type == "Linux":
        if iface_name:
            try:
                out = subprocess.run(
                    ["ip", "route", "show", "dev", iface_name],
                    capture_output=True, text=True, timeout=5
                )
                if out.returncode == 0:
                    for line in out.stdout.splitlines():
                        if "default" in line and "via" in line:
                            tokens = line.split()
                            for i, p in enumerate(tokens):
                                if p == "via" and i + 1 < len(tokens):
                                    return tokens[i + 1]
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass
        if computer_ip:
            try:
                out = subprocess.run(
                    ["ip", "route", "show", "default"],
                    capture_output=True, text=True, timeout=5
                )
                if out.returncode == 0 and out.stdout.strip():
                    parts = out.stdout.strip().split()
                    if "via" in parts:
                        idx = parts.index("via")
                        if idx + 1 < len(parts):
                            return parts[idx + 1]
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass
    if os_type == "Windows":
        try:
            out = subprocess.run(
                "ipconfig",
                shell=True, capture_output=True, text=True, timeout=10
            )
            if out.returncode != 0:
                return None
            lines = (out.stdout or "").splitlines()
            current_ip = None
            for line in lines:
                if line.strip() and not line.startswith(" ") and ":" in line:
                    current_ip = None
                if "IPv4 Address" in line and ":" in line:
                    current_ip = line.split(":")[-1].strip()
                if current_ip and "Default Gateway" in line and ":" in line:
                    gw = line.split(":")[-1].strip()
                    if gw and (computer_ip is None or current_ip == computer_ip):
                        return gw
        except (subprocess.TimeoutExpired, OSError):
            pass
    return None


def get_network_interfaces():
    """Get network interfaces and their IP addresses. Returns list of (iface_name, ip)."""
    if platform.system() == "Windows":
        result = subprocess.run("ipconfig", shell=True, capture_output=True, text=True, timeout=10)
        output = (result.stdout or "") if result.returncode == 0 else ""
        interfaces = []
        current_if = None
        for line in output.split("\n"):
            if ":" in line and "IPv" not in line:
                current_if = line.split(":")[0].strip()
            if "IPv4 Address" in line and current_if:
                ip = line.split(":")[1].strip()
                interfaces.append((current_if, ip))
        return interfaces
    else:
        result = subprocess.run(
            "ifconfig | grep 'inet ' | grep -v 127.0.0.1",
            shell=True, capture_output=True, text=True, timeout=5
        )
        output = (result.stdout or "") if result.returncode == 0 else ""
        interfaces = []
        for line in output.split("\n"):
            if line.strip():
                parts = line.strip().split()
                if len(parts) >= 2:
                    ip = parts[1]
                    iface = line.split(":")[0] if ":" in line else "unknown"
                    interfaces.append((iface, ip))
        return interfaces


def wait_for_ping(ip, timeout=300, interval=5, source_ip=None, log=print):
    """Wait for device to respond to ping."""
    ip = str(IPv4Address(ip))
    if source_ip:
        source_ip = str(IPv4Address(source_ip))
    system = platform.system()
    if system == "Windows":
        command = ["ping", "-n", "1", "-w", "1000"]
    else:
        command = ["ping", "-c", "1", "-W", "1000" if system == "Darwin" else "1"]
    if source_ip:
        command.extend(["-I" if system == "Linux" else "-S", source_ip])
    command.append(ip)
    log(f"Waiting for device {ip} to respond to ping...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = subprocess.run(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=min(3, deadline - time.monotonic()),
                creationflags=0x08000000 if system == "Windows" else 0,
                check=False,
            )
        except subprocess.TimeoutExpired:
            response = None
        except OSError as exc:
            log(f"Could not run ping: {exc}")
            return False
        if response is not None and response.returncode == 0:
            log(f"Device {ip} is responding to ping!")
            return True
        time.sleep(max(0, min(interval, deadline - time.monotonic())))
    log(f"Timeout waiting for device {ip} to respond to ping")
    return False
