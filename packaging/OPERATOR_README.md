# ADTRAN Firmware Upgrader Operator Guide

## What this app does

The desktop app guides one ADTRAN device at a time through firmware upgrade and device data recording. Completed device rows are appended to the selected CSV file.

## Before you start

1. Connect the computer to the ADTRAN device over Ethernet.
2. Keep Wi-Fi or VPN routes simple while flashing, if possible.
3. Open Settings and enter both the initial and upgraded SSH credentials.
4. Choose the ADTRAN firmware image for upgrade jobs.
5. Confirm the output CSV path.

The computer must have an IPv4 address on the device's subnet. DHCP is recommended when the device provides it. If using a static IP, configure an address for the initial network (usually `192.168.1.x`) and for the upgraded network (usually `172.16.192.x`) in Windows adapter settings. Entering `Local IP` in the app selects an existing address; it does not change Windows network settings.

## Normal workflow

1. Select `Upgrade firmware and record info`.
2. Choose the firmware image.
3. Leave the interface on auto-detect unless the app picks the wrong adapter.
4. Click `Start Device`.
5. Wait for the app to finish flashing, reconnecting, and recording device data.
6. Click `Next Device`, connect the next unit, and repeat.

Use `Record device info only` for devices that are already upgraded and only need their Wi-Fi, serial, MAC, and firmware data recorded.

## Notes for Windows

Windows may show a firewall prompt the first time the app starts the local firmware server. Allow access on the private/network connection used for the device.

Run the executable as your normal Windows user. Python is not required. The single-file app may take a few seconds to unpack on startup. Settings and credentials persist outside that temporary folder, and the default CSV is in `Documents\ADTRAN Firmware Upgrader\device_upgrades.csv` (or your redirected Documents folder).

Keep the CSV closed in Excel while processing devices. If it becomes locked, the app retains the current record and shows `Save Record`; close Excel and retry saving, or choose another CSV. Do not flash the same device again just to recover its data.

Wi-Fi passwords are masked in the table and logs but are included in the CSV. Store and share that file accordingly.

## Troubleshooting

- If Ethernet is not detected, select the connected adapter manually and confirm the device IP.
- If SSH fails, re-open Settings and verify both credential sets.
- If the upgraded device cannot be reached, confirm the upgraded IP, usually `172.16.192.1`.
- If firmware completion is not confirmed, leave the device powered and inspect its status before retrying. The app does not reset defaults or mark that attempt complete.
- If transfer fails, verify that Windows Firewall permits the app on the network profile used by the Ethernet connection. Do not turn off the firewall globally.
- If the operating system credential store is locked or unavailable, unlock your Windows session and retry Settings. Passwords are never saved to plain-text settings as a fallback.
- Use the live log when escalating an issue; it contains the device steps without exposing saved passwords.
