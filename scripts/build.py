"""Build SnipForge for Windows: SnipForge.exe (PyInstaller), a portable zip, and optionally the
installer (Inno Setup). Used by CI and locally.

    python scripts/build.py              # exe + portable zip
    python scripts/build.py --installer  # + SnipForge-<version>-setup.exe (needs Inno Setup 6)
    python scripts/build.py --installer --set-version 0.6.0-preview.1   # a preview build

The installer is also copied to SnipForge-setup.exe, which the release attaches so
https://github.com/nighthawk909/snipforge/releases/latest/download/SnipForge-setup.exe always
serves the newest stable installer. The in-app updater uses the versioned file.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUILD = ROOT / "build"
INIT_FILE = ROOT / "snipforge" / "__init__.py"
LATEST_INSTALLER = "SnipForge-setup.exe"
VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:-preview\.\d+)?")
VERSION_LINE_RE = re.compile(r'__version__\s*=\s*"([^"]+)"')
ISCC_CANDIDATES = [
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
]

VERSION_TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={tuple4}, prodvers={tuple4}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'SnipForge contributors'),
      StringStruct('FileDescription', 'SnipForge screenshot and markup tool'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'SnipForge'),
      StringStruct('LegalCopyright', 'MIT License'),
      StringStruct('OriginalFilename', 'SnipForge.exe'),
      StringStruct('ProductName', 'SnipForge'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def read_version(init_file: Path = INIT_FILE) -> str:
    match = VERSION_LINE_RE.search(init_file.read_text(encoding="utf-8"))
    if not match:
        sys.exit(f"Couldn't find __version__ in {init_file}")
    return match.group(1)


def stamp_version(version: str, init_file: Path = INIT_FILE) -> bytes:
    """Build as `version` (preview builds): write it into __version__ so the packaged app reports it.
    Returns the file's original bytes; main() puts them back after the build."""
    if not VERSION_RE.fullmatch(version):
        sys.exit(f"--set-version must look like 1.2.3 or 1.2.3-preview.4, got {version!r}")
    original = init_file.read_bytes()
    stamped, count = VERSION_LINE_RE.subn(f'__version__ = "{version}"', original.decode("utf-8"), count=1)
    if count != 1:
        sys.exit(f"Couldn't find __version__ in {init_file}")
    init_file.write_bytes(stamped.encode("utf-8"))
    return original


def numeric_version(version: str) -> str:
    """Windows file versions are four numbers: 0.5.0 -> 0.5.0.0, 0.6.0-preview.1 -> 0.6.0.1."""
    parts = [int(p) for p in re.findall(r"\d+", version)[:4]]
    return ".".join(str(p) for p in parts + [0] * (4 - len(parts)))


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def write_version_file(version: str) -> Path:
    tuple4 = tuple(int(p) for p in numeric_version(version).split("."))
    BUILD.mkdir(exist_ok=True)
    path = BUILD / "version_info.txt"
    path.write_text(VERSION_TEMPLATE.format(tuple4=tuple4, version=version), encoding="utf-8")
    return path


def build_exe(version: str) -> Path:
    os.environ["SNIPFORGE_VERSION_FILE"] = str(write_version_file(version))
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"),
         str(ROOT / "packaging" / "snipforge.spec")])
    app_dir = DIST / "SnipForge"
    if not (app_dir / "SnipForge.exe").exists():
        sys.exit("PyInstaller didn't produce SnipForge.exe")
    add_license_files(app_dir)
    add_theme_packs(app_dir)
    return app_dir


def add_license_files(app_dir: Path) -> None:
    """License texts sit next to SnipForge.exe so they're easy to find (LGPL notice requirement)."""
    shutil.copy2(ROOT / "LICENSE", app_dir / "LICENSE.txt")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", app_dir / "THIRD_PARTY_NOTICES.md")
    shutil.copytree(ROOT / "licenses", app_dir / "licenses", dirs_exist_ok=True)


def add_theme_packs(app_dir: Path, source: Path = ROOT / "themes") -> list[Path]:
    """Organization builds keep theme packs in themes/*.json at the repo root; they ship in a
    themes folder next to SnipForge.exe. A pack that doesn't validate fails the build."""
    if not source.is_dir():
        return []
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from snipforge.theme.packs import ThemePackError, load_pack

    copied = []
    for pack in sorted(source.glob("*.json")):
        try:
            load_pack(pack)
        except ThemePackError as exc:
            sys.exit(f"Theme pack {pack.name} is invalid: {exc}")
        (app_dir / "themes").mkdir(exist_ok=True)
        copied.append(Path(shutil.copy2(pack, app_dir / "themes" / pack.name)))
    return copied


def build_zip(app_dir: Path, version: str) -> Path:
    base = DIST / f"SnipForge-{version}-portable"
    return Path(shutil.make_archive(str(base), "zip", root_dir=app_dir.parent, base_dir=app_dir.name))


def find_iscc() -> Path:
    override = os.environ.get("ISCC")
    for candidate in ([Path(override)] if override else []) + ISCC_CANDIDATES:
        if candidate.exists():
            return candidate
    found = shutil.which("iscc")
    if found:
        return Path(found)
    sys.exit("Inno Setup 6 (ISCC.exe) not found. Install it or set the ISCC environment variable.")


def build_installer(app_dir: Path, version: str) -> Path:
    run([str(find_iscc()), f"/DAppVersion={version}", f"/DFileVersion={numeric_version(version)}",
         f"/DSourceDir={app_dir}", f"/O{DIST}", str(ROOT / "packaging" / "installer.iss")])
    return DIST / f"SnipForge-{version}-setup.exe"


def add_latest_alias(installer: Path) -> Path:
    """A version-less copy for the permanent .../releases/latest/download/SnipForge-setup.exe link."""
    alias = installer.with_name(LATEST_INSTALLER)
    shutil.copyfile(installer, alias)
    return alias


def write_checksums(files: list[Path], out_dir: Path = DIST) -> Path:
    lines = []
    for f in files:
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        lines.append(f"{digest}  {f.name}")
    out = out_dir / "SHA256SUMS.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def build_all(version: str, installer: bool) -> list[Path]:
    print(f"Building SnipForge {version}")
    shutil.rmtree(DIST, ignore_errors=True)
    app_dir = build_exe(version)
    outputs = [build_zip(app_dir, version)]
    if installer:
        setup = build_installer(app_dir, version)
        outputs = [*outputs, setup, add_latest_alias(setup)]
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", action="store_true", help="also build the Inno Setup installer")
    parser.add_argument("--set-version", metavar="VERSION",
                        help="build as this version, e.g. 0.6.0-preview.1 (snipforge/__init__.py is restored after)")
    args = parser.parse_args()
    original_init = stamp_version(args.set_version) if args.set_version else None
    try:
        outputs = build_all(read_version(), args.installer)
    finally:
        if original_init is not None:
            INIT_FILE.write_bytes(original_init)
    sums = write_checksums(outputs)
    print("\nBuilt:")
    for f in outputs + [sums]:
        print(f"  {f.relative_to(ROOT)}  ({f.stat().st_size / 1_048_576:.1f} MB)")


if __name__ == "__main__":
    main()
