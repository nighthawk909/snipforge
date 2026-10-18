"""Universal (Apple silicon and Intel) builds of dependencies that PyPI ships one architecture at a time.

SnipForge.app is one universal2 build, so PyInstaller needs every compiled file it bundles to hold
both arm64 and x86_64 code. Most dependencies (Python, PySide6, PyObjC) publish universal2 wheels;
the packages in SPLIT_WHEELS publish a separate wheel per architecture. Before PyInstaller runs,
scripts/build_mac.py fuses each one's compiled files (.so, .dylib) with `lipo`, from the arm64 and
x86_64 wheels of the version that's installed, into the installed package, and signs each again
(ad hoc). A package whose files are all universal already is left as it is, so running it twice
changes nothing. After PyInstaller, check_bundle() makes sure every compiled file in the app holds
both architectures, which also catches the next dependency that belongs in SPLIT_WHEELS.

macOS only (lipo comes with Xcode's command line tools, as on GitHub's macOS runners).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

# Distribution name -> the import package holding its compiled files. A new dependency with
# per-architecture macOS wheels is one line here.
SPLIT_WHEELS = {"pillow": "PIL"}
ARCHS = ("arm64", "x86_64")
MIN_MACOS = "14_0"  # LSMinimumSystemVersion in packaging/snipforge.spec
COMPILED = (".so", ".dylib")


def _run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def compiled_files(folder: Path) -> list[Path]:
    """The package's compiled files, including those delocate keeps in its hidden .dylibs folder."""
    return sorted(p for p in folder.rglob("*") if p.suffix in COMPILED and p.is_file() and not p.is_symlink())


def lipo_archs(path: Path) -> set[str]:
    result = subprocess.run(["lipo", "-archs", str(path)], capture_output=True, text=True, check=True)
    return set(result.stdout.split())


def is_universal(path: Path, archs: Callable[[Path], set[str]] = lipo_archs) -> bool:
    return set(ARCHS) <= archs(path)


def download_wheel(dist: str, version: str, arch: str, into: Path, run: Callable = _run) -> Path:
    """Downloads `dist==version`'s wheel for `arch` and this Python, unpacks it and returns the folder."""
    python = f"{sys.version_info.major}{sys.version_info.minor}"
    folder = into / arch
    run([sys.executable, "-m", "pip", "download", f"{dist}=={version}", "--no-deps", "--only-binary=:all:",
         "--platform", f"macosx_{MIN_MACOS}_{arch}", "--python-version", python, "--implementation", "cp",
         "--abi", f"cp{python}", "--dest", str(folder)])
    wheels = sorted(folder.glob("*.whl"))
    if len(wheels) != 1:
        sys.exit(f"Expected one {arch} wheel of {dist} {version}, got {[w.name for w in wheels]}")
    unpacked = into / f"{arch}-unpacked"
    with zipfile.ZipFile(wheels[0]) as wheel:
        wheel.extractall(unpacked)
    return unpacked


def fuse_plan(package: str, installed: Path, unpacked: dict[str, Path]) -> list[tuple[Path, list[Path]]]:
    """Each compiled file of the installed package, with the same file from every architecture's
    wheel. Exits if the wheels don't hold the same compiled files as what's installed."""
    wanted = {path.relative_to(installed) for path in compiled_files(installed)}
    for arch in ARCHS:
        found = {path.relative_to(unpacked[arch] / package) for path in compiled_files(unpacked[arch] / package)}
        if found != wanted:
            differ = sorted(str(p) for p in found ^ wanted)
            sys.exit(f"Can't make {package} universal: its {arch} wheel holds different files ({', '.join(differ)})")
    return [(installed / relative, [unpacked[arch] / package / relative for arch in ARCHS])
            for relative in sorted(wanted)]


def make_universal(dist: str, package: str, installed: Path, version: str, run: Callable = _run,
                   archs: Callable[[Path], set[str]] = lipo_archs, download: Callable = download_wheel) -> list[Path]:
    """Fuses the installed package's compiled files into universal ones. Returns the files it fused
    (none if they were universal already)."""
    files = compiled_files(installed)
    if not files:
        sys.exit(f"{package} has no compiled files in {installed}: is it installed?")
    if all(is_universal(f, archs) for f in files):
        return []
    with tempfile.TemporaryDirectory(prefix="snipforge-universal-") as tmp:
        unpacked = {arch: download(dist, version, arch, Path(tmp), run) for arch in ARCHS}
        plan = fuse_plan(package, installed, unpacked)
        for target, slices in plan:
            run(["lipo", "-create", *map(str, slices), "-output", str(target)])
            # Each slice keeps its own signature; sign the fused file again, ad hoc, so the Python that
            # runs PyInstaller (which loads Pillow for the icon) can always load it.
            run(["codesign", "--force", "--sign", "-", str(target)])
    thin = [str(f) for f in files if not is_universal(f, archs)]
    if thin:
        sys.exit(f"Still not universal after fusing: {', '.join(thin)}")
    return [target for target, _slices in plan]


def make_dependencies_universal() -> list[Path]:
    """Every SPLIT_WHEELS package, as installed for this Python."""
    from importlib import metadata, util

    fused = []
    for dist, package in SPLIT_WHEELS.items():
        spec = util.find_spec(package)
        if spec is None or not spec.submodule_search_locations:
            sys.exit(f"{dist} isn't installed (pip install -r requirements-dev.txt)")
        installed = Path(next(iter(spec.submodule_search_locations)))
        fused += make_universal(dist, package, installed, metadata.version(dist))
    return fused


def check_bundle(app: Path, archs: Callable[[Path], set[str]] = lipo_archs) -> tuple[list[Path], list[Path]]:
    """Every compiled file inside the built app holds both architectures, and the SPLIT_WHEELS
    packages' are there. Returns (all of them, the SPLIT_WHEELS ones); exits otherwise."""
    found = compiled_files(app)
    thin = [str(p.relative_to(app)) for p in found if not is_universal(p, archs)]
    if thin:
        sys.exit(f"Not universal in the app (add its package to SPLIT_WHEELS): {', '.join(thin)}")
    split = [p for p in found if any(package in p.relative_to(app).parts for package in SPLIT_WHEELS.values())]
    if not split:
        sys.exit(f"None of {', '.join(SPLIT_WHEELS.values())}'s compiled files are in {app}")
    return found, split
