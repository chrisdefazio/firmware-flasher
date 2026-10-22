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

- Python 3.11 (64-bit recommended for Windows builds)
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

4. For the GUI, enter credentials in Settings. No `.env` file is needed. For the legacy macOS/Linux CLI only, create a `.env` file in the project root:
```bash
# SSH Credentials for ADTRAN Device
INITIAL_USERNAME=initial_username
INITIAL_PASSWORD=initial_password
UPGRADED_USERNAME=new_username
UPGRADED_PASSWORD=new_password
```

## Usage

1. Place your firmware image files in the firmware_images directory
2. Run the GUI:
```bash
python adtran_gui.py
```
3. Open Settings, enter the initial and upgraded ADTRAN SSH credentials, select a firmware image, and click Start Device.

The original terminal menu is still available on macOS/Linux (its terminal-menu dependency does not support Windows):
```bash
python main.py
```
Follow the on-screen instructions to complete the firmware upgrade process or extract device information.

If a repo-local `venv` exists, `main.py` will automatically relaunch itself with that interpreter so the bundled dependencies and SSH behavior stay consistent.

## Desktop GUI

The GUI is ADTRAN-first and is designed for one-device-at-a-time bench work:

- Firmware picker for upgrade jobs
- Auto-detected or manually selected Ethernet interface
- Editable local IPv4 address; reconnects track the same adapter after DHCP changes
- Progress/status display and live SSH/device log
- Settings dialog for initial and upgraded credentials
- CSV output table for completed devices in the current session
- Open/export actions for the device CSV

Passwords are stored with the operating system credential store through `keyring`. Non-secret defaults such as IP addresses and output CSV path are stored with Qt settings.

SSH passwords and Wi-Fi keys are hidden in logs and the session table. The output CSV deliberately contains the Wi-Fi key for device provisioning, so treat CSV files as sensitive. A locked CSV can be saved again using `Save Record` without repeating the flash. Unknown or failed flash completion stops the job before restoring defaults or recording success.

## Packaging

Install packaging dependencies from `requirements.txt`.

For macOS, build the `.app` bundle with the checked-in spec:

```bash
pyinstaller adtran_gui.spec
```

This produces `dist/ADTRAN Firmware Upgrader.app`.

For Windows, run the onefile helper on a Windows machine or VM:

```powershell
py -3.11 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe tools\build_windows_onefile.py
.\venv\Scripts\python.exe tools\check_windows_build.py
```

This produces a single executable at `dist\ADTRAN Firmware Upgrader.exe`.

The intended Windows release target is Windows 10/11 x64. Build with 64-bit Python on Windows; PyInstaller does not cross-compile a Windows executable from macOS. Operators do not need Python installed. Build files stay in `build/windows`; the macOS spec is not overwritten.

Only regular `.img` and `.bin` files directly inside `firmware_images` are bundled when that directory exists. CSVs, `.env` files, subdirectories and symlinks are excluded. Review the images in that folder before building. The picker opens bundled images even when launched outside the checkout, and selections survive onefile extraction-directory changes. With no bundled images, operators can browse to a separate firmware file.

Windows onefile builds extract to a temporary folder when launched, so first startup can be slower. Unsigned onefile executables may also trigger stricter Windows Defender or SmartScreen warnings than the onedir build.

See `packaging/OPERATOR_README.md` for the short guide to ship with the app.

The `Windows desktop build` GitHub Actions workflow runs tests, builds the `.exe`, launches it from a temporary directory with a startup check, and uploads the executable and operator guide. It does not flash hardware or publish a release. A clean CI checkout has no bundled firmware because `firmware_images` is ignored.

Before release, complete the hardware and Windows checks in [the release checklist](packaging/WINDOWS_RELEASE_CHECKLIST.md). A successful build or startup check does not verify the SSH commands against a physical device.

## App icon

The app icon source is `assets/icon/adtran_modem_icon.svg`. Regenerate the checked-in PNG, `.ico`, and `.icns` assets with:

```bash
python tools/build_icon_assets.py
```

## SSH connection notes

- The desktop app uses native Qt network enumeration, independent of the language of Windows `ipconfig` output.
- ADTRAN SSH connections and firmware serving are bound to the selected local Ethernet address. The GUI never falls back to a different adapter when binding fails.
- Before the first SSH login, the app waits for ping. After a reboot it rereads the selected adapter's address and retries SSH while DHCP and the device recover.
- For `172.16.192.x` devices, it tries upgraded credentials first and then falls back to initial credentials if authentication fails.
- Rejected credentials stop immediately with a Settings error; transient network errors are retried.
- Only the selected firmware file is exposed by the HTTP server, not neighboring CSVs or other files. The server closes before the reboot wait.
