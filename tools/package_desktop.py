"""Create an allowlisted operator test ZIP on the native build platform."""

import hashlib
from importlib.metadata import distributions
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from firmware_upgrader import __version__


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    if sys.platform == "win32" and platform.machine().lower() in {"amd64", "x86_64"} and sys.maxsize > 2**32:
        target = "windows-x64"
        source = ROOT / "dist" / "ADTRAN Firmware Upgrader.exe"
    elif sys.platform == "darwin" and platform.machine() == "arm64":
        target = "macos-arm64"
        source = ROOT / "dist" / "ADTRAN Firmware Upgrader.app"
    else:
        raise SystemExit("Package on Windows x64 or Apple Silicon macOS.")
    if not source.exists():
        raise SystemExit(f"Build the app first: {source}")
    output = ROOT / "dist" / "releases"
    output.mkdir(parents=True, exist_ok=True)
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    name = f"ADTRAN-Firmware-Upgrader-{__version__}-{target}-test"
    archive = output / f"{name}.zip"
    if archive.exists():
        raise SystemExit(f"Archive already exists; move it aside before rebuilding: {archive}")
    with tempfile.TemporaryDirectory(prefix="package-", dir=build) as directory:
        stage = Path(directory) / name
        stage.mkdir()
        if source.is_dir():
            subprocess.run(["ditto", str(source), str(stage / source.name)], check=True)
        else:
            shutil.copy2(source, stage / source.name)
        for filename in ("OPERATOR_README.md", "TEST_BUILD_README.md"):
            shutil.copy2(ROOT / "packaging" / filename, stage / filename)
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT))
        metadata = {
            "version": __version__, "target": target, "source_commit": commit,
            "tracked_changes": dirty, "python": platform.python_version(),
            "build_os": platform.platform(), "status": "test build; hardware acceptance pending",
            "dependencies": sorted(f"{d.metadata['Name']}=={d.version}" for d in distributions()),
        }
        (stage / "BUILD_INFO.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        # Never sweep up checkout CSVs, credentials or unrelated files.
        files = sorted(p for p in stage.rglob("*") if p.is_file() and not p.is_symlink())
        sums = "".join(f"{sha256(p)}  {p.relative_to(stage).as_posix()}\n" for p in files)
        (stage / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")
        if sys.platform == "darwin":
            # Preserve framework symlinks and executable permissions.
            subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(stage), str(archive)], check=True)
        else:
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
                for path in sorted(stage.iterdir()):
                    bundle.write(path, f"{name}/{path.name}")
    checksum = archive.with_suffix(".zip.sha256")
    checksum.write_text(f"{sha256(archive)}  {archive.name}\n", encoding="utf-8")
    print(archive)
    print(checksum)


if __name__ == "__main__":
    main()
