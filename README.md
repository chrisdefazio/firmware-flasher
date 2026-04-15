# Modem / Router Firmware Upgrader

A Python utility for upgrading firmware on a variety of devices used by ISPs and other network providers.

Currently supported devices:
- ADTRAN 834 v6
- ADTRAN 834 v5
- COMTREND VR3071
- COMTREND VR3071v2

## Features

- Automated firmware upgrade process
- SSH-based device communication
- Progress monitoring
- Automatic IP detection and connection
- WiFi configuration retrieval

## Prerequisites

- Python 3.9 or higher
- pip (Python package installer)
- SSH access to the devices you want to upgrade or extract information from

## Installation

1. Clone the repository:
```bash
git clone https://github.com/chrisdefazio/firmware-flasher.git
cd firmware-flasher
```

2. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows, use: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Create a `.env` file in the project root with your device credentials:
```bash
# SSH Credentials for ADTRAN Device
INITIAL_USERNAME=initial_username
INITIAL_PASSWORD=initial_password
UPGRADED_USERNAME=new_username
UPGRADED_PASSWORD=new_password
```

## Usage

1. Place your firmware image files in the firmware_images directory
2. Run the script:
```bash
python main.py
```
3. Follow the on-screen instructions to complete the firmware upgrade process or extract device information

If a repo-local `venv` exists, `main.py` will automatically relaunch itself with that interpreter so the bundled dependencies and SSH behavior stay consistent.

## SSH connection notes

- The ADTRAN workflow now performs a TCP preflight check to `device_ip:22` before Paramiko authentication starts.
- ADTRAN SSH connections are bound to the detected local Ethernet IP so Paramiko uses the same source interface as the selected device link.
- Before the first ADTRAN SSH login, the tool waits for the device to answer ping and then gives SSH a short warm-up period.
- For `172.16.192.x` devices, it tries upgraded credentials first and then falls back to initial credentials if authentication fails.
- If required environment credentials are missing, the tool will stop early with a clear message instead of retrying indefinitely.
- During retries, only missing-credential failures are treated as non-retryable; transient network reachability errors continue retrying.