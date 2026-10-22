"""Check the native Apple Silicon app outside the checkout, without devices."""

import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile


def main() -> None:
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise SystemExit("Run this check on an Apple Silicon Mac with arm64 Python.")
    root = Path(__file__).resolve().parents[1]
    app = root / "dist" / "ADTRAN Firmware Upgrader.app"
    executable = app / "Contents" / "MacOS" / "ADTRAN Firmware Upgrader"
    architectures = subprocess.check_output(["lipo", "-archs", str(executable)], text=True).strip()
    if architectures != "arm64":
        raise SystemExit(f"Expected an arm64 executable, found: {architectures}")
    # Verifies bundle integrity, not Developer ID signing or notarization.
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    with tempfile.TemporaryDirectory(prefix="ADTRAN check \u00e9 ") as directory:
        report_path = Path(directory) / "startup report.json"
        result = subprocess.run(
            [str(executable), "--smoke-test", str(report_path)], cwd=directory,
            stdin=subprocess.DEVNULL, timeout=120, check=False,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )
        if result.returncode or not report_path.is_file():
            raise SystemExit("Packaged app did not complete its startup check.")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if not report.get("ok") or not report.get("frozen"):
            raise SystemExit(f"Packaged startup check failed: {report}")
        print(json.dumps({**report, "architecture": architectures}, indent=2))


if __name__ == "__main__":
    main()
