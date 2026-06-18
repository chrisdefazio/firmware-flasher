# ADTRAN Firmware Upgrader Operator Guide

## What this app does

The desktop app guides one ADTRAN device at a time through firmware upgrade and device data recording. Completed device rows are appended to the selected CSV file.

## Before you start

1. Connect the computer to the ADTRAN device over Ethernet.
2. Keep Wi-Fi or VPN routes simple while flashing, if possible.
3. Open Settings and enter both the initial and upgraded SSH credentials.
4. Choose the ADTRAN firmware image for upgrade jobs.
5. Confirm the output CSV path.

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

## Troubleshooting

- If Ethernet is not detected, select the connected adapter manually and confirm the device IP.
- If SSH fails, re-open Settings and verify both credential sets.
- If the upgraded device cannot be reached, confirm the upgraded IP, usually `172.16.192.1`.
- Use the live log when escalating an issue; it contains the device steps without exposing saved passwords.
