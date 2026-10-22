"""Launch the Windows onefile build outside the checkout and verify startup."""

import json
import os
from pathlib import Path
import subprocess
import tempfile


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    executable = root / "dist" / "ADTRAN Firmware Upgrader.exe"
    if os.name != "nt":
        raise SystemExit("This packaged executable check must run on Windows.")
    # Exercise path quoting and Unicode without using the operator's preferences.
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
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
