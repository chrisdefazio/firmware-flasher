# Windows Release Validation

Status: Windows execution and physical-device validation are required before release. macOS tests and a macOS package build cannot establish that the Windows executable works on operator hardware.

## Automated checks

- Run `python -m unittest discover -s tests -v` on Windows. Tests use fake SSH responses and a loopback HTTP server; they do not connect to a modem or use saved credentials.
- Build with `python tools/build_windows_onefile.py` on Windows x64 using Python 3.11 x64.
- Run `python tools/check_windows_build.py`. This starts the actual executable outside the checkout, exercises paths with spaces/non-ASCII text, and checks window construction, icons and credential-backend availability. It does not read or write real credentials.
- Confirm the `Windows desktop build` Actions job succeeds. Its artifact is an unsigned test build, not a published release.

## Operator acceptance

- Launch the executable on a clean Windows 10/11 x64 machine with no Python installed, as a standard user. Confirm the custom icon, attribution/version and readable layout at 100% and 150% display scaling.
- Save both credential pairs, close/reopen the app, and confirm they persist in Windows Credential Manager. Confirm wrong passwords produce a clear Settings error promptly.
- Connect the intended Ethernet or USB adapter with Wi-Fi/VPN also enabled. Confirm its name and address, automatic selection on the device subnet, and explicit selection when more than one address matches. Repeat with a non-English Windows installation if one is supported in your environment.
- Choose bundled firmware, restart the onefile app, and confirm the selection still resolves. Also select an external image from a path containing spaces.
- Verify the firewall permits firmware HTTP transfer on the Ethernet network profile. Verify a blocked transfer fails clearly, without restoring defaults or adding a successful CSV row.
- On a test ADTRAN 834 v5 and v6 with approved firmware, verify download, flashing, actual completion text, reset, reboot, DHCP subnet change and reconnect. Confirm model, serial, MAC and firmware version against the physical device. Completion detection intentionally requires an explicit upgrade/flash success message; capture a redacted transcript if the firmware uses different wording.
- Record an already-upgraded device in info-only mode. Confirm UTF-8 SSIDs and Wi-Fi keys in the CSV and masked passwords in the GUI/log.
- Open the CSV in Excel before starting: verify the preflight error prevents flashing. Open it during a job: verify `Save Record` preserves the result, writes once after unlocking, and does not repeat the flash.
- Try closing during a job: the app should stay open. After completion, process several devices with `Next Device` and check exactly one appended row per completed device.
- Test missing firmware, empty firmware, unplugged Ethernet, wrong IP and a reboot that does not recover. None should be marked complete or close the app with a traceback.

## Distribution

- Review bundled `.img`/`.bin` files before building; only ship firmware approved for the target models. CI artifacts have no firmware bundled unless it is supplied separately.
- Ship the executable and operator guide. Do not ship build directories, `.env` files, CSV records or the source checkout as the operator package.
- Decide whether to code-sign the executable for your distribution channel. An unsigned executable may show SmartScreen prompts; a successful build does not establish signing or reputation.
- Record the commit, dependency versions (`python -m pip freeze`), Windows version, adapter model, firmware filename and tested device revisions for the release.
