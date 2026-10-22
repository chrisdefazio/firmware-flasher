# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path


root = Path(SPECPATH)
datas = []
firmware_dir = root / "firmware_images"
if firmware_dir.is_dir():
    for path in sorted(firmware_dir.iterdir()):
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in {".img", ".bin"}:
            datas.append((str(path), "firmware_images"))
if (root / "assets/icon").exists():
    datas.append((str(root / "assets/icon"), "assets/icon"))

mac_icon = root / "assets/icon/adtran_modem_icon.icns"
windows_icon = root / "assets/icon/adtran_modem_icon.ico"

hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtNetwork",
]
hiddenimports.append("keyring.backends.macOS" if sys.platform == "darwin" else "keyring.backends.Windows")

a = Analysis(
    [str(root / "adtran_gui.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ADTRAN Firmware Upgrader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(windows_icon) if sys.platform.startswith("win") and windows_icon.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ADTRAN Firmware Upgrader",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="ADTRAN Firmware Upgrader.app",
        icon=str(mac_icon) if mac_icon.exists() else None,
        bundle_identifier="com.firmwaretools.adtran-firmware-upgrader",
    )
