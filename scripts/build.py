"""Build SnipForge for Windows: SnipForge.exe (PyInstaller), a portable zip, and optionally the
installer (Inno Setup). Used by CI and locally.

    python scripts/build.py              # exe + portable zip
    python scripts/build.py --installer  # + SnipForge-<version>-setup.exe (needs Inno Setup 6)
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


def read_version() -> str:
    text = (ROOT / "snipforge" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not match:
        sys.exit("Couldn't find __version__ in snipforge/__init__.py")
    return match.group(1)


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def write_version_file(version: str) -> Path:
    parts = [int(p) for p in re.findall(r"\d+", version)[:4]]
    tuple4 = tuple(parts + [0] * (4 - len(parts)))
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
    run([str(find_iscc()), f"/DAppVersion={version}", f"/DSourceDir={app_dir}", f"/O{DIST}",
         str(ROOT / "packaging" / "installer.iss")])
    return DIST / f"SnipForge-{version}-setup.exe"


def write_checksums(files: list[Path]) -> Path:
    lines = []
    for f in files:
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        lines.append(f"{digest}  {f.name}")
    out = DIST / "SHA256SUMS.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", action="store_true", help="also build the Inno Setup installer")
    args = parser.parse_args()
    version = read_version()
    print(f"Building SnipForge {version}")
    shutil.rmtree(DIST, ignore_errors=True)
    app_dir = build_exe(version)
    outputs = [build_zip(app_dir, version)]
    if args.installer:
        outputs.append(build_installer(app_dir, version))
    sums = write_checksums(outputs)
    print("\nBuilt:")
    for f in outputs + [sums]:
        print(f"  {f.relative_to(ROOT)}  ({f.stat().st_size / 1_048_576:.1f} MB)")


if __name__ == "__main__":
    main()
