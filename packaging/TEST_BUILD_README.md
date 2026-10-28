# Desktop test build

This package is for IT validation. Physical-device acceptance and clean-machine
operator checks remain required before general company distribution. Automated
startup tests do not establish that flashing works on a physical ADTRAN device.

- Windows: extract the ZIP to a local folder, then open the `.exe`. No Python
  installation or administrator account is required. The executable is unsigned.
- macOS: extract the ZIP on an Apple Silicon Mac and copy the `.app` to a local
  Applications folder. No Python installation is required. This test bundle has
  an ad-hoc signature; it is not Developer ID signed or notarized. If macOS blocks
  it, contact IT. Company distribution needs signing/notarization or an approved
  managed deployment method.
- Use the firmware supplied by IT for the exact device model/revision. Firmware
  is supplied separately from CI packages; do not assume an 834 v6 image works on
  an 834 v5. The desktop GUI supports ADTRAN; the legacy COMTREND CLI is not part
  of this desktop package.
- Enter credentials in Settings on each computer. No credentials or device CSV
  records are included. Use a local output CSV while processing devices.

`BUILD_INFO.json` records the source revision and installed dependencies.
`SHA256SUMS.txt` records packaged file hashes. The adjacent `.zip.sha256` verifies
the archive. See `OPERATOR_README.md` for the device workflow.
