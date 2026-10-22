# Desktop release preparation

Targets: Windows 10/11 x64 and Apple Silicon macOS. The Mac CI build runs on
macOS 15; verify any other company macOS version on an actual operator machine.

## Build and package

1. Check out the same source commit on both native platforms.
2. Create a Python 3.11 virtual environment and install `requirements.txt`.
3. Run `python -m unittest discover -s tests -v`.
4. On Windows, run `python tools/build_windows_onefile.py`, then
   `python tools/check_windows_build.py`.
5. On Apple Silicon, run `python -m PyInstaller --noconfirm --clean adtran_gui.spec`,
   then `python tools/check_macos_build.py`.
6. Run `python tools/package_desktop.py`. Retain the ZIP and adjacent checksum.

The GitHub Actions workflows perform these steps for pull requests and main.
Downloaded Mac ZIPs should be extracted on a Mac, preserving the app's symlinks
and executable permissions. Never rebuild a Mac app bundle by unzipping and
rezipping it on Windows.

## Before general use

- Complete `WINDOWS_RELEASE_CHECKLIST.md`, including real ADTRAN 834 v5/v6 tests
  for each model that will be supported. Test firmware only on its approved model.
- Repeat a flash, reboot/reconnect and info-only job on an Apple Silicon operator
  Mac. Confirm Keychain persistence, Ethernet selection with Wi-Fi/VPN active,
  firmware transfer, CSV output and error handling.
- Test each package on a standard-user machine without Python installed.
- Resolve Windows signing/SmartScreen handling and macOS Developer ID signing
  and notarization (or company-managed deployment) before calling the packages
  ready for all staff. The CI artifacts are test builds.
- Record firmware filename and SHA-256, device revision, adapter model, OS,
  source commit, test results and approval in the internal release record.

## Suggested company share layout

```text
ADTRAN Firmware Upgrader/<version>/
  Windows/<Windows ZIP and checksum>
  macOS-Apple-Silicon/<Mac ZIP and checksum>
  Firmware/ADTRAN-834-v6/<approved image and checksum>
  OPERATOR_README.md
  VALIDATION.md
```

Keep firmware outside the applications so one approved image can serve both
platforms. Users copy/extract their app locally and browse to the approved
firmware. Use a local writable output CSV during each bench session. Do not
place credentials, `.env`, source checkouts or device CSVs in the app release
folder. Keep the release in an IT test location until hardware checks pass.
