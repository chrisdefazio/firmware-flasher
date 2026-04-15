import os
import subprocess
import http.server
import socketserver
import threading
import time
import platform
import socket
import paramiko
import getpass
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from dotenv import load_dotenv
import csv
from datetime import datetime
from simple_term_menu import TerminalMenu
from paramiko.ssh_exception import NoValidConnectionsError

from network_utils import (
    drain_tty_input,
    get_gateway_for_connection,
    get_network_interfaces,
    get_wired_interface_ip,
    wait_for_ethernet_connection,
    wait_for_ping,
)

# Load environment variables from .env file
load_dotenv()

# Configure HTTP server for firmware hosting
class SimpleHTTPServerThread(threading.Thread):
    def __init__(self, port=8000, directory=None):
        threading.Thread.__init__(self)
        self.port = port
        self.directory = directory
        self.daemon = True  # Daemon thread will close when the main program exits
        
    def run(self):
        if self.directory:
            os.chdir(self.directory)
            
        handler = http.server.SimpleHTTPRequestHandler
        with socketserver.TCPServer(("", self.port), handler) as httpd:
            print(f"HTTP server running at port {self.port}")
            httpd.serve_forever()

def run_command(command):
    """Run a shell command and return the output"""
    try:
        result = subprocess.run(command, shell=True, check=True, 
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
                               text=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {e}")
        return None

def clear_ssh_key(ip):
    """Clear SSH known hosts for the IP"""
    if platform.system() == "Windows":
        print(f"On Windows, manually remove the key for {ip} from ~/.ssh/known_hosts if needed")
    else:
        run_command(f"ssh-keygen -R {ip}")
        print(f"Cleared SSH key for {ip}")

def mask_secret(secret):
    """Mask a secret for safe console output."""
    if not secret:
        return "<empty>"
    if len(secret) <= 2:
        return "*" * len(secret)
    return f"{secret[:2]}{'*' * (len(secret) - 2)}"

def test_ssh_port_reachability(ip, port=22, timeout=5, source_ip=None):
    """Preflight check to distinguish TCP reachability issues from SSH auth failures."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        if source_ip:
            sock.bind((source_ip, 0))
        sock.connect((ip, port))
        with sock:
            local_host, local_port = sock.getsockname()
            print(
                f"TCP preflight succeeded: local {local_host}:{local_port} -> {ip}:{port}"
            )
            return True, "ok"
    except socket.timeout:
        return False, f"tcp_timeout: unable to reach {ip}:{port} within {timeout}s"
    except ConnectionRefusedError:
        return False, f"tcp_refused: {ip}:{port} refused the connection"
    except OSError as err:
        errno_text = f" errno={err.errno}" if getattr(err, "errno", None) is not None else ""
        return False, f"tcp_os_error:{errno_text} {err}"

def get_ssh_credential_candidates(ip, username=None, password=None):
    """Build SSH credential candidates and validate non-empty values."""
    if username is not None or password is not None:
        explicit_user = username or ""
        explicit_password = password or ""
        if not explicit_user or not explicit_password:
            print("Provided SSH credentials are incomplete. Username and password are both required.")
            return []
        return [(explicit_user, explicit_password, "provided credentials")]

    initial_user = os.getenv("INITIAL_USERNAME") or ""
    initial_password = os.getenv("INITIAL_PASSWORD") or ""
    upgraded_user = os.getenv("UPGRADED_USERNAME") or ""
    upgraded_password = os.getenv("UPGRADED_PASSWORD") or ""

    candidates = []
    seen = set()

    def add_candidate(user, pwd, label):
        if not user or not pwd:
            return
        key = (user, pwd)
        if key not in seen:
            candidates.append((user, pwd, label))
            seen.add(key)

    if ip.startswith("172.16.192."):
        print("Using upgraded firmware credentials first (172.16.192.x IP range)")
        add_candidate(upgraded_user, upgraded_password, "upgraded")
        add_candidate(initial_user, initial_password, "initial fallback")
    else:
        print("Using initial firmware credentials first")
        add_candidate(initial_user, initial_password, "initial")
        add_candidate(upgraded_user, upgraded_password, "upgraded fallback")

    if not candidates:
        print("No valid SSH credentials found in environment.")
        print("Set INITIAL_USERNAME/INITIAL_PASSWORD and/or UPGRADED_USERNAME/UPGRADED_PASSWORD in .env")
    return candidates

def ssh_connect_with_shell(ip, username=None, password=None, timeout=10, prompt_on_fail=False, source_ip=None):
    """Connect to device via SSH and return client and channel
    
    Args:
        ip: Device IP address
        username: SSH username (if None, will be determined from IP)
        password: SSH password (if None, will be determined from IP)
        timeout: Connection timeout in seconds
        prompt_on_fail: If True, prompt user for manual credentials on auth failure
        source_ip: Optional local source IP to bind before connecting
    """
    print(f"Connecting to {ip} via SSH...")
    if source_ip:
        print(f"Binding SSH connection source to local IP: {source_ip}")
    can_reach_ssh, preflight_message = test_ssh_port_reachability(
        ip,
        timeout=max(3, min(timeout, 10)),
        source_ip=source_ip,
    )
    if not can_reach_ssh:
        print(f"TCP preflight failed before Paramiko auth: {preflight_message}")
        if source_ip:
            default_route_ok, default_route_message = test_ssh_port_reachability(
                ip,
                timeout=max(3, min(timeout, 10)),
                source_ip=None,
            )
            if default_route_ok:
                print("Default-route preflight succeeded without source binding.")
            else:
                print(f"Default-route preflight also failed: {default_route_message}")
        print("Continuing anyway: attempting Paramiko connection in case this is transient.")

    credential_candidates = get_ssh_credential_candidates(ip, username, password)
    if not credential_candidates:
        return None, None, "missing_credentials"
    
    def attempt_connection(user, pwd, credential_label):
        """Attempt SSH connection with given credentials"""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        print(f"Trying credential set: {credential_label} (username='{user}', password='{mask_secret(pwd)}')")

        # Update handler to use current password
        def handler(title, instructions, prompt_list):
            responses = []
            for prompt, echo in prompt_list:
                responses.append(pwd)
            return responses

        try:
            bound_sock = None
            if source_ip:
                try:
                    bound_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    bound_sock.bind((source_ip, 0))
                    bound_sock.settimeout(timeout)
                    bound_sock.connect((ip, 22))
                    print(f"Using bound source socket for Paramiko: {source_ip}")
                except OSError as bind_err:
                    print(
                        f"Bound source socket setup failed for {source_ip}: {bind_err}. "
                        "Falling back to default routing."
                    )
                    try:
                        bound_sock.close()
                    except Exception:
                        pass
                    bound_sock = None

            # First try standard password authentication
            connect_kwargs = {
                "username": user,
                "password": pwd,
                "timeout": timeout,
                "banner_timeout": timeout,
                "auth_timeout": timeout,
                "allow_agent": False,
                "look_for_keys": False,
            }
            if bound_sock is not None:
                connect_kwargs["sock"] = bound_sock

            client.connect(
                ip,
                **connect_kwargs,
            )
            print(f"Successfully connected to {ip} (password auth)")

            # Create an interactive shell
            channel = client.invoke_shell()
            channel.settimeout(timeout)

            # Wait for initial prompt
            time.sleep(2)
            initial_output = b""
            while channel.recv_ready():
                chunk = channel.recv(4096)
                initial_output += chunk
            
            initial_str = initial_output.decode('utf-8', errors='ignore')
            print("\n----- Initial SSH Connection Output -----")
            print(initial_str)
            print("-----------------------------------------\n")
            
            return client, channel, "connected"  # Success
        except paramiko.AuthenticationException as auth_err:
            print(f"Password authentication failed for '{user}' ({credential_label}): {auth_err}")
            print("Attempting keyboard-interactive authentication...")

            # Close the failed connection and try keyboard-interactive
            try:
                client.close()
            except:
                pass
            
            try:
                # Get transport for keyboard-interactive auth
                if source_ip:
                    try:
                        kbd_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        kbd_sock.bind((source_ip, 0))
                        kbd_sock.settimeout(timeout)
                        kbd_sock.connect((ip, 22))
                        transport = paramiko.Transport(kbd_sock)
                        transport.start_client(timeout=timeout)
                        print(f"Using bound source socket for keyboard-interactive auth: {source_ip}")
                    except OSError as bind_err:
                        print(
                            f"Bound keyboard-interactive socket failed for {source_ip}: {bind_err}. "
                            "Falling back to default routing."
                        )
                        transport = paramiko.Transport((ip, 22))
                        transport.connect()
                else:
                    transport = paramiko.Transport((ip, 22))
                    transport.connect()
                transport.auth_interactive(user, handler)

                # Create client from transport
                client = paramiko.SSHClient()
                client._transport = transport
                print(f"Successfully connected to {ip} (keyboard-interactive auth)")

                # Create an interactive shell
                channel = transport.open_session()
                channel.get_pty()
                channel.invoke_shell()
                channel.settimeout(timeout)

                # Wait for initial prompt
                time.sleep(2)
                initial_output = b""
                while channel.recv_ready():
                    chunk = channel.recv(4096)
                    initial_output += chunk
                
                initial_str = initial_output.decode('utf-8', errors='ignore')
                print("\n----- Initial SSH Connection Output -----")
                print(initial_str)
                print("-----------------------------------------\n")
                
                return client, channel, "connected"
            except paramiko.AuthenticationException as kbd_auth_err:
                print(f"Keyboard-interactive authentication failed: {kbd_auth_err}")
                return None, None, "auth_failed"
            except Exception as kbd_err:
                print(f"Keyboard-interactive authentication also failed: {kbd_err}")
                return None, None, "socket_error"
        except NoValidConnectionsError as conn_err:
            print(f"Socket connection failed for {ip}:22 ({conn_err})")
            if hasattr(conn_err, "errors") and conn_err.errors:
                for dest, error in conn_err.errors.items():
                    dest_host, dest_port = dest
                    err_no = getattr(error, "errno", None)
                    print(
                        f"  - {dest_host}:{dest_port} -> {type(error).__name__}"
                        f"{f' errno={err_no}' if err_no is not None else ''}: {error}"
                    )
            return None, None, "socket_error"
        except paramiko.SSHException as ssh_err:
            print(f"SSH error connecting to {ip}: {ssh_err}")
            return None, None, "ssh_error"
        except socket.timeout:
            print(f"Connection to {ip} timed out after {timeout} seconds")
            return None, None, "timeout"
        except socket.error as sock_err:
            print(f"Socket error connecting to {ip}: {sock_err}")
            return None, None, "socket_error"
        except Exception as e:
            print(f"Unexpected error connecting to {ip}: {type(e).__name__}: {e}")
            return None, None, "unknown_error"

    last_error = "unknown_error"
    for candidate_user, candidate_password, candidate_label in credential_candidates:
        client, channel, result = attempt_connection(candidate_user, candidate_password, candidate_label)
        if client and channel:
            return client, channel, None
        last_error = result

    # If auth failed for all env/provided credentials and prompt_on_fail is enabled, ask for manual credentials.
    if last_error == "auth_failed" and prompt_on_fail:
        print("\n" + "=" * 50)
        print("AUTHENTICATION FAILED - Enter credentials manually")
        print("=" * 50)
        fallback_user = credential_candidates[0][0]
        new_username = input(f"Username [{fallback_user}]: ").strip() or fallback_user
        new_password = getpass.getpass("Password: ")
        if not new_password:
            print("Manual password entry was empty; skipping manual retry.")
            return None, None, last_error

        print(f"\nRetrying with username: '{new_username}'")
        client, channel, result = attempt_connection(new_username, new_password, "manual")
        if client and channel:
            return client, channel, None
        last_error = result

    return None, None, last_error

def retry_ssh_connect(
    ip,
    username=None,
    password=None,
    max_attempts=10,
    retry_delay=10,
    prompt_on_auth_fail=True,
    source_ip=None,
):
    """Attempt to connect via SSH with multiple retries
    
    Args:
        ip: Device IP address
        username: SSH username (if None, will be determined from IP)
        password: SSH password (if None, will be determined from IP)
        max_attempts: Maximum number of connection attempts
        retry_delay: Delay between retries in seconds
        prompt_on_auth_fail: If True, prompt for manual credentials on first auth failure
        source_ip: Optional local source IP to bind before connecting
    """
    print(f"Attempting to connect to {ip} via SSH (max {max_attempts} attempts)...")
    
    prompted_for_creds = False
    
    for attempt in range(1, max_attempts + 1):
        print(f"\nAttempt {attempt}/{max_attempts}...")
        
        # On first attempt, allow prompting for credentials if auth fails
        # After manual entry, use those credentials for subsequent retries
        should_prompt = prompt_on_auth_fail and not prompted_for_creds
        
        ssh_client, channel, error = ssh_connect_with_shell(
            ip,
            username,
            password,
            prompt_on_fail=should_prompt,
            source_ip=source_ip,
        )
        
        if ssh_client and channel:
            print(f"✓ Successfully connected to {ip} on attempt {attempt}")
            return ssh_client, channel
        
        # Mark that we've given the user a chance to enter credentials
        if should_prompt and error == "auth_failed":
            prompted_for_creds = True

        if error in ("missing_credentials",):
            print("Stopping retries because this failure will not improve without user/system changes.")
            break
        
        if attempt < max_attempts:
            print(f"Connection failed. Waiting {retry_delay} seconds before retrying...")
            time.sleep(retry_delay)
    
    print(f"✗ Failed to connect to {ip} after {max_attempts} attempts")
    return None, None

def execute_ssh_command(channel, command, wait_time=5, max_output_wait=30):
    """Execute command and get full output"""
    if not channel:
        return None
    
    # Clear any pending output
    while channel.recv_ready():
        channel.recv(4096)
    
    # Send command
    print(f"\n>>> Executing command: {command}")
    channel.send(command + "\n")
    
    # Collect output with timeout
    output = b""
    start_time = time.time()
    last_receive_time = start_time
    
    # First short wait
    time.sleep(1)
    
    while (time.time() - last_receive_time < wait_time and 
           time.time() - start_time < max_output_wait):
        if channel.recv_ready():
            chunk = channel.recv(4096)
            output += chunk
            print(chunk.decode('utf-8', errors='ignore'), end='')
            sys.stdout.flush()  # Force output to display immediately
            last_receive_time = time.time()
        else:
            time.sleep(0.1)
    
    # Convert to string
    output_str = output.decode('utf-8', errors='ignore')
    
    # Print summary
    duration = time.time() - start_time
    print(f"\n\n>>> Command completed in {duration:.2f} seconds")
    
    return output_str

def safe_close_ssh_connection(ssh_client, channel):
    """Safely close SSH connection and channel"""
    if channel:
        try:
            channel.close()
        except:
            pass
    
    if ssh_client:
        try:
            ssh_client.close()
        except:
            pass
    
    # Give time for connection to fully terminate
    time.sleep(2)

def wait_for_initial_ssh_ready(device_ip, ping_timeout=60, ping_interval=3, ssh_warmup_seconds=5):
    """Give the initially connected device time to become reachable before SSH."""
    if wait_for_ping(device_ip, timeout=ping_timeout, interval=ping_interval):
        print(f"Waiting {ssh_warmup_seconds} seconds for SSH service to start...")
        time.sleep(ssh_warmup_seconds)
        return True

    print(
        f"Device {device_ip} did not respond to ping within {ping_timeout} seconds. "
        "Continuing to SSH retries anyway."
    )
    return False

def monitor_upgrade_progress(channel, timeout=300):
    """Monitor the upgrade process until completion or timeout"""
    print("\n----- Monitoring Upgrade Progress -----")
    
    start_time = time.time()
    last_output = ""
    download_started = False
    upgrade_complete = False
    
    while time.time() - start_time < timeout:
        if channel.recv_ready():
            output = channel.recv(4096).decode('utf-8', errors='ignore')
            print(output, end='')
            sys.stdout.flush()
            last_output += output
            
            # Check for progress indicators
            if "download" in output.lower() or "transfer" in output.lower() or "%" in output:
                download_started = True
            
            if "success" in output.lower() or "complete" in output.lower():
                upgrade_complete = True
                print("\n\n>>> Upgrade completed successfully!")
                break
                
            if "error" in output.lower() or "fail" in output.lower():
                print("\n\n>>> Upgrade failed!")
                break
        else:
            # If no data available, wait briefly
            time.sleep(0.5)
    
    print("\n----- End of Monitoring -----\n")
    
    if time.time() - start_time >= timeout:
        print("\n>>> Monitoring timed out!")
    
    return download_started, upgrade_complete, last_output

def main():
    # Check for paramiko library
    try:
        import paramiko
    except ImportError:
        print("The paramiko library is required. Please install it using:")
        print("pip install paramiko")
        return
    
    # Ask user what they want to do
    operation_options = [
        "Upgrade firmware and extract device information",
        "Extract device information only (no upgrade)",
    ]
    operation_menu = TerminalMenu(operation_options, title="ADTRAN DEVICE UTILITY")
    choice_index = operation_menu.show()
    drain_tty_input()
    if choice_index is None:
        print("Cancelled.")
        return
    choice = "1" if choice_index == 0 else "2"

    # Connection mode: Auto Detect or Manual (select at start; can switch to manual during auto-detect)
    mode_options = ["Auto Detect (recommended)", "Manual"]
    mode_menu = TerminalMenu(mode_options, title="Connection mode:")
    mode_index = mode_menu.show()
    drain_tty_input()
    if mode_index is None:
        print("Cancelled.")
        return
    auto_detect_mode = mode_index == 0

    operation_succeeded = False

    if choice == "1":
        # Offer two options for firmware selection
        print("\n===== FIRMWARE FILE SELECTION =====")
        selection_options = ["Select from firmware_images directory", "Enter custom file path"]
        selection_menu = TerminalMenu(selection_options, title="Choose firmware source:")
        selection_index = selection_menu.show()
        drain_tty_input()
        if selection_index is None:
            print("Firmware selection cancelled.")
            return
        
        firmware_path = None
        
        if selection_index == 0:
            # Select from firmware_images directory
            firmware_images_dir = "firmware_images"
            
            if not os.path.exists(firmware_images_dir):
                print(f"Error: {firmware_images_dir} directory not found")
                return
            
            # Get list of firmware files
            firmware_files = [f for f in os.listdir(firmware_images_dir) 
                             if os.path.isfile(os.path.join(firmware_images_dir, f))]
            
            if not firmware_files:
                print(f"Error: No firmware files found in {firmware_images_dir} directory")
                return
            
            # Display menu for firmware selection
            print(f"\nSelect a firmware file from {firmware_images_dir}:")
            firmware_menu = TerminalMenu(firmware_files, title="")
            firmware_index = firmware_menu.show()
            drain_tty_input()
            if firmware_index is None:
                print("Firmware selection cancelled.")
                return
            
            firmware_filename = firmware_files[firmware_index]
            firmware_path = os.path.join(firmware_images_dir, firmware_filename)
        else:
            # Enter custom file path
            firmware_path = input("\nEnter the path to the firmware image file: ")
            if not firmware_path:
                print("No file path entered.")
                return
        
        if not os.path.exists(firmware_path):
            print(f"Error: File {firmware_path} not found")
            return
        
        # Start HTTP server for firmware hosting
        # We'll serve from the directory containing the firmware file
        firmware_dir = os.path.dirname(os.path.abspath(firmware_path))
        if not firmware_dir:  # If the file is in the current directory
            firmware_dir = os.getcwd()
        
        firmware_filename = os.path.basename(firmware_path)
        
        print(f"\nStarting HTTP server in: {firmware_dir}")
        print(f"Serving firmware file: {firmware_filename}")
        
        server_thread = SimpleHTTPServerThread(port=8000, directory=firmware_dir)
        server_thread.start()
    
    # Display instructions for connecting the gateway
    print("\n===== STEP 1: CONNECT DEVICE =====")
    print("Please follow these instructions:")
    print("1. Plug in the 834_v6 gateway to power")
    print("2. Connect the gateway to this computer over ethernet")
    print("3. Wait for the status light to blink blue/green indicating it's fully booted")

    computer_ip = None
    selected_iface = None
    if auto_detect_mode:
        print("\nDetecting Ethernet connection (you can press Enter to switch to manual selection)...")
        detected = wait_for_ethernet_connection(timeout=300, interval=3)
        if detected:
            iface_name, computer_ip = detected
            selected_iface = iface_name
            print(f"Ethernet detected: {iface_name} -> {computer_ip}")
    if not auto_detect_mode or computer_ip is None:
        if not auto_detect_mode:
            input("Press Enter when the device is ready to continue...\n")
        # Display network interfaces and prompt for computer IP (manual or fallback)
        print("\n===== STEP 2: CONFIRM NETWORK CONNECTION =====")
        interfaces = get_network_interfaces()
        interface_options = [f"{iface}: {ip}" for (iface, ip) in interfaces]
        interface_menu = TerminalMenu(interface_options, title="Select the interface connected to the device")
        idx = interface_menu.show()
        drain_tty_input()
        if idx is None:
            print("Cancelled.")
            return
        selected_iface = interfaces[idx][0]
        computer_ip = interfaces[idx][1]
    print(f"Using computer IP: {computer_ip}")

    # Default device/gateway IP: detect from interface when possible
    default_device_ip = "192.168.1.1"
    gateway = get_gateway_for_connection(selected_iface, computer_ip)
    if gateway:
        default_device_ip = gateway
        print(f"Detected gateway address: {default_device_ip}")
    if auto_detect_mode and gateway:
        device_ip = default_device_ip
        print(f"Using device IP: {device_ip}")
    else:
        device_ip = input(f"\nEnter the device IP address (default: {default_device_ip}): ") or default_device_ip
    
    # Clear SSH key for device IP
    clear_ssh_key(device_ip)

    print("\n===== STEP 2.5: WAIT FOR DEVICE NETWORK =====")
    wait_for_initial_ssh_ready(device_ip)
    
    if choice == "1":
        # Connect to device via SSH and perform upgrade
        print("\n===== STEP 3: UPGRADING FIRMWARE =====")
        print(f"Connecting to device at {device_ip}...")
        
        # SSH connection with interactive shell and automatic credential selection
        ssh_client, channel = retry_ssh_connect(
            device_ip,
            max_attempts=5,
            retry_delay=10,
            prompt_on_auth_fail=True,
            source_ip=computer_ip,
        )
        if not ssh_client or not channel:
            print("Failed to connect to device. Please check the connection and try again.")
            return
        
        # Execute system restore nvram command to clear RAM before upgrade
        print("\nExecuting system restore nvram command to clear RAM...")
        output = execute_ssh_command(channel, "system restore nvram")
        
        # Monitor for confirmation prompt
        if "confirm" in output.lower() or "proceed" in output.lower() or "y/n" in output.lower():
            print("Confirmation prompt detected. Sending 'y'...")
            output = execute_ssh_command(channel, "y")
        
        # Execute upgrade command
        upgrade_url = f"http://{computer_ip}:8000/{firmware_filename}"
        output = execute_ssh_command(channel, f"upgrade {upgrade_url}")
        
        # Monitor for confirmation prompt
        if "confirm" in output.lower() or "proceed" in output.lower() or "y/n" in output.lower():
            print("Second confirmation prompt detected. Sending 'y'...")
            output = execute_ssh_command(channel, "y")
        
        # Monitor the upgrade progress
        print("\nStarting to monitor upgrade progress...")
        download_started, upgrade_complete, last_output = monitor_upgrade_progress(channel)
        
        if download_started:
            print("✓ Firmware download was initiated")
        else:
            print("✗ Firmware download may not have started")
        
        if upgrade_complete:
            print("✓ Upgrade process completed successfully")
        else:
            print("⚠ Upgrade completion confirmation not received")
            print("The upgrade may still be in progress or may have failed.")
        
        # Execute system restore default command
        print("\nExecuting system restore default command...")
        output = execute_ssh_command(channel, "system restore default")
        
        # Monitor for confirmation prompt
        if "confirm" in output.lower() or "proceed" in output.lower() or "y/n" in output.lower():
            print("Confirmation prompt detected. Sending 'y'...")
            output = execute_ssh_command(channel, "y")
        
        # Safely close SSH connection before device reboots
        print("\nSafely closing SSH connection...")
        safe_close_ssh_connection(ssh_client, channel)
        
        # Instructions for waiting for reboot
        print("\n===== STEP 4: WAITING FOR DEVICE REBOOT =====")
        print("The device is now performing a system restore and will reboot automatically.")
        print("Please wait while the device completes this process...")
        
        # Prompt for new device IP
        print("\n===== STEP 5: CONNECT TO UPGRADED DEVICE =====")
        print("The device IP has changed. The new IP should be in the 172.16.192.x range.")
        new_device_ip = input("Enter the new device IP address (default: 172.16.192.1): ") or "172.16.192.1"
        
        # Wait for device to respond
        if wait_for_ping(new_device_ip):
            # Give services time to start
            print(f"\nDevice is responding to ping. Waiting 30 seconds for all services to start...")
            time.sleep(30)
            
            # Clear SSH key for new device IP
            clear_ssh_key(new_device_ip)
            
            # Connect to upgraded device with retries
            print(f"\nConnecting to upgraded device at {new_device_ip}...")
            ssh_client, channel = retry_ssh_connect(new_device_ip, source_ip=computer_ip)
            
            if ssh_client and channel:
                extract_device_info(ssh_client, channel, new_device_ip, operation_type="Upgrade")
                ssh_client.close()
                operation_succeeded = True
            
            # Final instructions for web configuration
            print("\n===== STEP 6: COMPLETE SETUP VIA WEB GUI =====")
            print(f"Open a web browser and navigate to: http://{new_device_ip}")
            print("Set Intellifi mode to 'Mesh Controller'")
            print("Login and confirm the router is working as expected")
        else:
            print(f"Unable to reach device at {new_device_ip}. Please check the connection and try again.")
    else:
        # Just extract device information
        print("\n===== EXTRACTING DEVICE INFORMATION =====")
        print(f"Connecting to device at {device_ip}...")
        
        # Connect to device with retries and automatic credential selection
        ssh_client, channel = retry_ssh_connect(
            device_ip,
            max_attempts=5,
            retry_delay=15,
            source_ip=computer_ip,
        )
        
        if ssh_client and channel:
            extract_device_info(ssh_client, channel, device_ip, operation_type="Info Only")
            ssh_client.close()
            operation_succeeded = True
        else:
            print("Failed to connect to device. Please check the connection and try again.")
    
    print("\n===== OPERATION COMPLETE =====")
    if choice == "1" and operation_succeeded:
        print("The device has been successfully upgraded and configured.")
    elif choice != "1" and operation_succeeded:
        print("Device information has been successfully extracted.")
    else:
        print("Operation did not complete successfully.")
    
    if choice == "1":
        print("\nThe HTTP server is still running. Press Ctrl+C to exit the program when you're finished.")

def extract_device_info(ssh_client, channel, device_ip, operation_type="Info Only"):
    """Extract and save device information"""
    # Get WiFi configuration
    print("\nRetrieving WiFi configuration...")
    wifi_output = execute_ssh_command(channel, "show wifi config")
    
    # Extract WiFi SSID and password
    ssid = ""
    wifi_key = ""
    if wifi_output:
        for line in wifi_output.split('\n'):
            if "wireless.i5g.ssid" in line:
                ssid = line.split("=")[1].strip().strip("'")
            if "wireless.i5g.key" in line:
                wifi_key = line.split("=")[1].strip().strip("'")
    
    if ssid and wifi_key:
        print("\n===== WIFI CONFIGURATION =====")
        print(f"SSID: {ssid}")
        print(f"Password: {wifi_key}")
    
    # Get device info
    print("\nRetrieving device information...")
    mfg_output = execute_ssh_command(channel, "show mfg")
    
    # Extract serial and MAC
    serial = ""
    mac = ""
    if mfg_output:
        for line in mfg_output.split('\n'):
            if "MFG_SERIAL" in line:
                serial = line.split("=")[1].strip()
            if "MFG_MAC" in line:
                mac = line.split("=")[1].strip()
    
    if serial and mac:
        print("\n===== DEVICE INFORMATION =====")
        print(f"Serial Number: {serial}")
        print(f"MAC Address: {mac}")
    
    # Get firmware build information
    print("\nRetrieving firmware build information...")
    build_output = execute_ssh_command(channel, "show buildinfo")
    
    # Extract firmware description
    firmware_version = ""
    if build_output:
        for line in build_output.split('\n'):
            if "DISTRIB_DESCRIPTION" in line:
                firmware_version = line.split("=")[1].strip().strip("'")
                print(f"Firmware Version: {firmware_version}")
    
    # Save the information to a CSV file
    csv_file = "device_upgrades.csv"
    file_exists = os.path.isfile(csv_file)
    
    try:
        with open(csv_file, 'a', newline='') as f:
            writer = csv.writer(f)
            # Write header if file is new
            if not file_exists:
                writer.writerow(['Timestamp', 'Device IP', 'WiFi SSID', 'WiFi Password', 'Serial Number', 'MAC Address', 'Firmware Version', 'Operation Type'])
            
            # Write device information
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            writer.writerow([timestamp, device_ip, ssid, wifi_key, serial, mac, firmware_version, operation_type])
            
        print(f"\nDevice information appended to {csv_file}")
    except Exception as e:
        print(f"Error saving device information: {e}")

if __name__ == "__main__":
    try:
        main()
        # Keep the main thread running so the HTTP server stays alive
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting program...")