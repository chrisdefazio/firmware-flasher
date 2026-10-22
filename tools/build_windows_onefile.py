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
    "PySide6.QtNetwork",
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
        "--noupx",
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(ROOT / "build" / "windows"),
        "--specpath",
        str(ROOT / "build" / "windows"),
        "--name",
        APP_NAME,
        "--icon",
        str(ICON_PATH),
        "--add-data",
        add_data_arg(ICON_ASSETS_DIR, "assets/icon"),
    ]

    if FIRMWARE_IMAGES_DIR.is_dir():
        for path in sorted(FIRMWARE_IMAGES_DIR.iterdir()):
            if path.is_file() and not path.is_symlink() and path.suffix.lower() in {".img", ".bin"}:
                command.extend(["--add-data", add_data_arg(path, "firmware_images")])

    for module_name in HIDDEN_IMPORTS:
        command.extend(["--hidden-import", module_name])

    command.append(str(ENTRYPOINT))
    return command


def build_environment() -> dict[str, str]:
    """Keep unrelated PATH DLLs out of the frozen application's dependency scan."""
    env = os.environ.copy()
    windows = Path(os.environ.get("SystemRoot", os.environ.get("WINDIR", r"C:\Windows")))
    env["PATH"] = os.pathsep.join(str(path) for path in (
        Path(sys.executable).parent,
        Path(sys.base_prefix),
        Path(sys.base_prefix) / "DLLs",
        windows / "System32",
        windows,
    ))
    return env


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Windows onefile ADTRAN Firmware Upgrader executable."
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Print the PyInstaller command without running it.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    command = build_command()
    print(subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command))

    if args.print_only:
        return 0

    if os.name != "nt":
        print(
            "Not running PyInstaller because this is not Windows. "
            "Run this helper on Windows to create dist\\ADTRAN Firmware Upgrader.exe."
        )
        return 0

    for path in (ENTRYPOINT, ICON_PATH, ICON_ASSETS_DIR / "adtran_modem_icon_256.png"):
        if not path.is_file():
            print(f"Missing build asset: {path}", file=sys.stderr)
            return 1
    try:
        subprocess.run(command, cwd=ROOT, env=build_environment(), check=True)
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    print(f"Built {ROOT / 'dist' / f'{APP_NAME}.exe'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
