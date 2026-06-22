#!/usr/bin/env python3
"""Build the Windows onefile executable for the ADTRAN Firmware Upgrader.

Run this on Windows from the repo root:

    .\\venv\\Scripts\\python.exe tools\\build_windows_onefile.py

PyInstaller does not cross-compile Windows executables from macOS/Linux. On
non-Windows platforms this script prints the command it would run and exits.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "ADTRAN Firmware Upgrader"
ENTRYPOINT = ROOT / "adtran_gui.py"
ICON_PATH = ROOT / "assets" / "icon" / "adtran_modem_icon.ico"
ICON_ASSETS_DIR = ROOT / "assets" / "icon"
FIRMWARE_IMAGES_DIR = ROOT / "firmware_images"

HIDDEN_IMPORTS = [
    "keyring.backends.Windows",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
]


def add_data_arg(source: Path, destination: str) -> str:
    return f"{source}{os.pathsep}{destination}"


def build_command() -> list[str]:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        APP_NAME,
        "--icon",
        str(ICON_PATH),
        "--add-data",
        add_data_arg(ICON_ASSETS_DIR, "assets/icon"),
    ]

    if FIRMWARE_IMAGES_DIR.exists():
        command.extend(
            [
                "--add-data",
                add_data_arg(FIRMWARE_IMAGES_DIR, "firmware_images"),
            ]
        )

    for module_name in HIDDEN_IMPORTS:
        command.extend(["--hidden-import", module_name])

    command.append(str(ENTRYPOINT))
    return command


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Windows onefile ADTRAN Firmware Upgrader executable."
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Print the PyInstaller command without running it.",
    )
    parser.add_argument(
        "--allow-non-windows",
        action="store_true",
        help="Run PyInstaller even when this script is not running on Windows.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    command = build_command()
    print(shlex.join(command))

    if args.print_only:
        return 0

    if os.name != "nt" and not args.allow_non_windows:
        print(
            "Not running PyInstaller because this is not Windows. "
            "Run this helper on Windows to create dist\\ADTRAN Firmware Upgrader.exe."
        )
        return 0

    subprocess.run(command, cwd=ROOT, check=True)
    print(f"Built {ROOT / 'dist' / f'{APP_NAME}.exe'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
