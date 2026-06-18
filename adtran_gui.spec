# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path


datas = []
if Path("firmware_images").exists():
    datas.append(("firmware_images", "firmware_images"))
if Path("assets/icon").exists():
    datas.append(("assets/icon", "assets/icon"))

mac_icon = Path("assets/icon/adtran_modem_icon.icns")
windows_icon = Path("assets/icon/adtran_modem_icon.ico")

hiddenimports = [
    "keyring.backends.macOS",
    "keyring.backends.Windows",
    "keyring.backends.SecretService",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
]

a = Analysis(
    ["adtran_gui.py"],
    pathex=[],
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
