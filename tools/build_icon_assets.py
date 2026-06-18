#!/usr/bin/env python3
"""Build app icon assets from the source SVG."""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = ROOT / "assets" / "icon"
SVG_PATH = ICON_DIR / "adtran_modem_icon.svg"
PNG_PATH = ICON_DIR / "adtran_modem_icon_256.png"
ICO_PATH = ICON_DIR / "adtran_modem_icon.ico"
ICNS_PATH = ICON_DIR / "adtran_modem_icon.icns"

PNG_SIZES = [16, 24, 32, 48, 64, 128, 256, 512, 1024]
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def render_png(renderer: QSvgRenderer, size: int, output_path: Path) -> None:
    image = QImage(QSize(size, size), QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    renderer.render(painter)
    painter.end()
    if not image.save(str(output_path), "PNG"):
        raise RuntimeError(f"Could not write {output_path}")


def write_ico(png_paths: list[Path], output_path: Path) -> None:
    image_data = [path.read_bytes() for path in png_paths]
    header_size = 6 + 16 * len(image_data)
    offset = header_size
    entries = []
    for path, data in zip(png_paths, image_data):
        size = int(path.stem.rsplit("_", 1)[-1])
        width = 0 if size >= 256 else size
        height = 0 if size >= 256 else size
        entries.append(struct.pack("<BBBBHHII", width, height, 0, 0, 1, 32, len(data), offset))
        offset += len(data)

    with output_path.open("wb") as handle:
        handle.write(struct.pack("<HHH", 0, 1, len(image_data)))
        for entry in entries:
            handle.write(entry)
        for data in image_data:
            handle.write(data)


def write_icns(png_by_type: list[tuple[str, Path]], output_path: Path) -> None:
    chunks = []
    for icon_type, path in png_by_type:
        data = path.read_bytes()
        chunks.append(icon_type.encode("ascii") + struct.pack(">I", len(data) + 8) + data)
    total_length = 8 + sum(len(chunk) for chunk in chunks)
    with output_path.open("wb") as handle:
        handle.write(b"icns")
        handle.write(struct.pack(">I", total_length))
        for chunk in chunks:
            handle.write(chunk)


def build_icns(renderer: QSvgRenderer, output_path: Path) -> None:
    iconutil = shutil.which("iconutil")

    iconset = ICON_DIR / "adtran_modem_icon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True)

    iconset_sizes = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }
    for filename, size in iconset_sizes.items():
        render_png(renderer, size, iconset / filename)

    try:
        if iconutil and sys.platform == "darwin":
            try:
                subprocess.run(
                    [iconutil, "-c", "icns", str(iconset), "-o", str(output_path)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return
            except subprocess.CalledProcessError:
                print("iconutil rejected the iconset; falling back to direct ICNS writing.")

        write_icns(
            [
                ("icp4", iconset / "icon_16x16.png"),
                ("icp5", iconset / "icon_32x32.png"),
                ("icp6", iconset / "icon_32x32@2x.png"),
                ("ic07", iconset / "icon_128x128.png"),
                ("ic08", iconset / "icon_256x256.png"),
                ("ic09", iconset / "icon_512x512.png"),
                ("ic10", iconset / "icon_512x512@2x.png"),
            ],
            output_path,
        )
    finally:
        shutil.rmtree(iconset, ignore_errors=True)


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication.instance() or QGuiApplication([])
    del app

    renderer = QSvgRenderer(str(SVG_PATH))
    if not renderer.isValid():
        raise RuntimeError(f"Invalid SVG: {SVG_PATH}")

    rendered_pngs: list[Path] = []
    for size in PNG_SIZES:
        output_path = ICON_DIR / f"adtran_modem_icon_{size}.png"
        render_png(renderer, size, output_path)
        rendered_pngs.append(output_path)

    write_ico([ICON_DIR / f"adtran_modem_icon_{size}.png" for size in ICO_SIZES], ICO_PATH)
    build_icns(renderer, ICNS_PATH)

    expected = [PNG_PATH, ICO_PATH]
    if sys.platform == "darwin":
        expected.append(ICNS_PATH)
    missing = [path for path in expected if not path.exists()]
    if missing:
        raise RuntimeError(f"Missing generated icon assets: {missing}")

    print(f"Wrote {PNG_PATH}")
    print(f"Wrote {ICO_PATH}")
    if ICNS_PATH.exists():
        print(f"Wrote {ICNS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
