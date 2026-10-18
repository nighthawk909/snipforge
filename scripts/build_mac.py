"""Build SnipForge for macOS: SnipForge.app (PyInstaller) in a SnipForge-<version>.dmg. Used by the
manually run "macOS build" workflow and on a Mac.

    python scripts/build_mac.py                                # dist/SnipForge.app + the .dmg
    python scripts/build_mac.py --set-version 0.9.0-preview.1  # a preview build

Free tools only. The app gets an ad-hoc signature (Apple Silicon refuses to run code with none),
not a Developer ID one, and isn't notarized, so the first launch needs "Open Anyway" in System
Settings; the .dmg carries a note saying how (docs/MAC-PLAN.md). Runs on macOS only: it uses
codesign, ditto and hdiutil, which come with macOS.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402 - sibling script: version stamping, license files, checksums
import universal  # noqa: E402 - sibling script: universal2 builds of single-architecture dependencies

ROOT, DIST, BUILD = build.ROOT, build.DIST, build.BUILD
APP_NAME = "SnipForge.app"
DMG_ATTEMPTS = 3
FIRST_RUN_NOTE = "If macOS won't open SnipForge.txt"
FIRST_RUN_TEXT = """\
SnipForge is free and open source. It isn't signed with a paid Apple Developer ID, so the first
time you open it macOS says it can't check it for malicious software. To open it:

1. Drag SnipForge into Applications (or into the Applications folder in your home folder if you
   can't write to the main one), then open it. macOS says it can't verify SnipForge: click Done.
2. Open System Settings > Privacy & Security, scroll down to Security, and click "Open Anyway"
   next to the message about SnipForge. Enter your Mac password if asked.
3. Click "Open Anyway" once more. From then on SnipForge opens like any other app.

To take snips, turn SnipForge on in System Settings > Privacy & Security > Screen & System Audio
Recording (Screen Recording on macOS 14) when macOS asks, then quit and reopen SnipForge.

Work Mac? Your IT team may block apps that aren't from the App Store or an identified developer.
Ask them to allow SnipForge (source code and checksums: {repo_url}).
"""


def bundle_version(version: str) -> str:
    """macOS bundle versions are up to three numbers: 0.9.0-preview.1 -> 0.9.0."""
    match = re.match(r"\d+\.\d+\.\d+", version)
    if not match:
        sys.exit(f"Can't make a bundle version from {version!r}")
    return match.group(0)


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def build_app(version: str) -> Path:
    os.environ["SNIPFORGE_VERSION"] = bundle_version(version)
    # The app is universal2; Pillow (and anything else in universal.SPLIT_WHEELS) comes from PyPI
    # one architecture at a time, so its compiled files are fused first (docs/CROSS-PLATFORM.md).
    for fused in universal.make_dependencies_universal():
        print(f"Made universal: {fused}")
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"),
         str(ROOT / "packaging" / "snipforge.spec")])
    app = DIST / APP_NAME
    if not (app / "Contents" / "MacOS" / "SnipForge").exists():
        sys.exit(f"PyInstaller didn't produce {APP_NAME}")
    compiled, split = universal.check_bundle(app)
    print(f"All {len(compiled)} compiled files in the app hold {' and '.join(universal.ARCHS)}, including:")
    for bundled in split:
        print(f"  {bundled.relative_to(app)}")
    resources = app / "Contents" / "Resources"
    build.add_license_files(resources)  # LGPL notice: license texts ship with the app
    build.add_theme_packs(resources)
    # Adding files broke PyInstaller's signature over the bundle: sign it again, ad hoc (free).
    run(["codesign", "--force", "--deep", "--sign", "-", str(app)])
    run(["codesign", "--verify", "--deep", "--strict", str(app)])
    return app


def build_dmg(app: Path, version: str) -> Path:
    """A compressed disk image with the app, an Applications shortcut to drag it to, and the
    first-run note."""
    dmg = DIST / f"SnipForge-{version}.dmg"
    with tempfile.TemporaryDirectory(prefix="snipforge-dmg-") as tmp:
        staging = Path(tmp) / "SnipForge"
        staging.mkdir()
        run(["ditto", str(app), str(staging / APP_NAME)])  # keeps the bundle's symlinks and signature
        (staging / "Applications").symlink_to("/Applications")
        note = FIRST_RUN_TEXT.format(repo_url=build.project.REPO_URL)
        (staging / FIRST_RUN_NOTE).write_text(note, encoding="utf-8")
        create = ["hdiutil", "create", "-volname", f"SnipForge {version}", "-srcfolder", str(staging),
                  "-fs", "HFS+", "-format", "UDZO", "-ov", str(dmg)]
        for attempt in range(1, DMG_ATTEMPTS + 1):
            try:
                run(create)
                break
            except subprocess.CalledProcessError:
                if attempt == DMG_ATTEMPTS:
                    raise
                print(f"hdiutil failed (often 'Resource busy' on CI); trying again ({attempt}/{DMG_ATTEMPTS})")
                time.sleep(5 * attempt)
    return dmg


def main() -> None:
    if sys.platform != "darwin":
        sys.exit("build_mac.py runs on macOS; on Windows use scripts/build.py")
    parser = argparse.ArgumentParser()
    parser.add_argument("--set-version", metavar="VERSION",
                        help="build as this version, e.g. 0.9.0-preview.1 (snipforge/__init__.py is restored after)")
    args = parser.parse_args()
    original_init = build.stamp_version(args.set_version) if args.set_version else None
    try:
        version = build.read_version()
        print(f"Building SnipForge {version} for macOS")
        shutil.rmtree(DIST, ignore_errors=True)
        dmg = build_dmg(build_app(version), version)
    finally:
        if original_init is not None:
            build.INIT_FILE.write_bytes(original_init)
    sums = build.write_checksums([dmg])
    print("\nBuilt:")
    for f in (dmg, sums):
        print(f"  {f.relative_to(ROOT)}  ({f.stat().st_size / 1_048_576:.1f} MB)")


if __name__ == "__main__":
    main()
