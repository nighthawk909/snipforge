"""scripts/universal.py: the Mac app is universal2, so dependencies PyPI ships one architecture at a
time (Pillow) are fused from both wheels before PyInstaller runs. lipo and pip are faked here; the
macOS CI job does it for real and its self-test runs both halves of the app."""
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("universal", ROOT / "scripts" / "universal.py")
universal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(universal)

FILES = ("_imaging.cpython-314-darwin.so", "_imagingmath.cpython-314-darwin.so", ".dylibs/libjpeg.62.dylib")


def package(folder: Path, files=FILES, extra: str = "") -> Path:
    for name in files:
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(extra or name)
    (folder / "Image.py").write_text("# not compiled")
    return folder


class FakeMac:
    """lipo and pip download: `archs` says which architectures each file holds."""

    def __init__(self, tmp: Path) -> None:
        self.tmp, self.archs, self.commands, self.downloads = tmp, {}, [], []

    def lipo_archs(self, path: Path) -> set[str]:
        return self.archs.get(Path(path), {"arm64"})

    def run(self, cmd):
        self.commands.append(cmd)
        if cmd[:2] == ["lipo", "-create"]:
            output = Path(cmd[cmd.index("-output") + 1])
            shutil.copy(cmd[2], output)
            self.archs[output] = {"arm64", "x86_64"}

    def download(self, dist, version, arch, into, run):
        self.downloads.append((dist, version, arch))
        return package(into / f"{arch}-unpacked" / "PIL").parent


def test_pillow_is_listed_and_the_app_needs_both_architectures():
    assert universal.SPLIT_WHEELS["pillow"] == "PIL" and universal.ARCHS == ("arm64", "x86_64")


def test_finds_compiled_files_including_delocate_s_hidden_folder(tmp_path):
    found = universal.compiled_files(package(tmp_path / "PIL"))
    assert sorted(p.relative_to(tmp_path / "PIL").as_posix() for p in found) == sorted(FILES)


def test_fuses_every_compiled_file_from_both_wheels(tmp_path):
    mac = FakeMac(tmp_path)
    installed = package(tmp_path / "site" / "PIL")
    fused = universal.make_universal("pillow", "PIL", installed, "12.3.0", run=mac.run, archs=mac.lipo_archs,
                                     download=mac.download)
    assert mac.downloads == [("pillow", "12.3.0", "arm64"), ("pillow", "12.3.0", "x86_64")]
    assert sorted(p.relative_to(installed).as_posix() for p in fused) == sorted(FILES)
    lipo = [cmd for cmd in mac.commands if cmd[:2] == ["lipo", "-create"]]
    assert len(lipo) == len(FILES)
    for cmd in lipo:  # the arm64 slice, then the x86_64 one, into the installed file
        assert "arm64-unpacked" in cmd[2] and "x86_64-unpacked" in cmd[3] and cmd[4] == "-output"


def test_already_universal_downloads_nothing(tmp_path):
    mac = FakeMac(tmp_path)
    installed = package(tmp_path / "PIL")
    for path in universal.compiled_files(installed):
        mac.archs[path] = {"x86_64", "arm64"}
    assert universal.make_universal("pillow", "PIL", installed, "12.3.0", run=mac.run, archs=mac.lipo_archs,
                                    download=mac.download) == []
    assert mac.downloads == [] and mac.commands == []


def test_wheels_with_other_files_stop_the_build(tmp_path):
    installed = package(tmp_path / "PIL")
    wheels = {"arm64": package(tmp_path / "a" / "PIL").parent,
              "x86_64": package(tmp_path / "x" / "PIL", files=FILES[:2]).parent}  # a different build
    with pytest.raises(SystemExit, match="x86_64 wheel holds different files.*libjpeg"):
        universal.fuse_plan("PIL", installed, wheels)


def test_a_file_still_thin_after_fusing_stops_the_build(tmp_path):
    mac = FakeMac(tmp_path)
    mac.run = lambda cmd: mac.commands.append(cmd)  # lipo "succeeds" but nothing changes
    with pytest.raises(SystemExit, match="Still not universal"):
        universal.make_universal("pillow", "PIL", package(tmp_path / "PIL"), "12.3.0", run=mac.run,
                                 archs=mac.lipo_archs, download=mac.download)


def test_fused_files_are_signed_again(tmp_path):
    mac = FakeMac(tmp_path)
    installed = package(tmp_path / "PIL")
    universal.make_universal("pillow", "PIL", installed, "12.3.0", run=mac.run, archs=mac.lipo_archs,
                             download=mac.download)
    signed = [cmd[-1] for cmd in mac.commands if cmd[:4] == ["codesign", "--force", "--sign", "-"]]
    assert sorted(signed) == sorted(str(p) for p in universal.compiled_files(installed))


def test_every_compiled_file_in_the_built_app_is_checked(tmp_path):
    mac = FakeMac(tmp_path)
    app = tmp_path / "SnipForge.app"
    frameworks = app / "Contents" / "Frameworks"
    package(frameworks / "PIL")
    package(frameworks / "PySide6", files=("QtCore.abi3.so",))
    for path in universal.compiled_files(frameworks / "PIL"):
        mac.archs[path] = {"arm64", "x86_64"}
    with pytest.raises(SystemExit, match=r"Not universal in the app.*QtCore"):  # the next split-wheel package
        universal.check_bundle(app, archs=mac.lipo_archs)
    mac.archs[frameworks / "PySide6" / "QtCore.abi3.so"] = {"arm64", "x86_64"}
    compiled, split = universal.check_bundle(app, archs=mac.lipo_archs)
    assert len(compiled) == len(FILES) + 1 and len(split) == len(FILES)
    without_pillow = tmp_path / "Other.app"
    package(without_pillow / "PySide6", files=("QtCore.abi3.so",))
    mac.archs[without_pillow / "PySide6" / "QtCore.abi3.so"] = {"arm64", "x86_64"}
    with pytest.raises(SystemExit, match="None of PIL"):
        universal.check_bundle(without_pillow, archs=mac.lipo_archs)


def test_downloads_the_installed_version_for_each_architecture(tmp_path):
    import zipfile

    asked = []

    def pip(cmd):
        asked.append(cmd)
        dest = Path(cmd[cmd.index("--dest") + 1])
        dest.mkdir(parents=True)
        with zipfile.ZipFile(dest / "pillow-12.3.0-cp314-cp314-macosx_11_0_arm64.whl", "w") as wheel:
            wheel.writestr("PIL/_imaging.cpython-314-darwin.so", b"arm64 code")

    unpacked = universal.download_wheel("pillow", "12.3.0", "arm64", tmp_path, run=pip)
    assert (unpacked / "PIL" / "_imaging.cpython-314-darwin.so").read_bytes() == b"arm64 code"
    cmd = asked[0]
    python = f"{sys.version_info.major}{sys.version_info.minor}"
    assert cmd[:5] == [sys.executable, "-m", "pip", "download", "pillow==12.3.0"]
    assert {"--no-deps", "--only-binary=:all:"} <= set(cmd)
    for flag, value in (("--platform", "macosx_14_0_arm64"), ("--python-version", python),
                        ("--implementation", "cp"), ("--abi", f"cp{python}")):
        assert cmd[cmd.index(flag) + 1] == value
    with pytest.raises(SystemExit, match="Expected one x86_64 wheel"):
        universal.download_wheel("pillow", "12.3.0", "x86_64", tmp_path, run=lambda cmd: None)


def test_a_missing_package_stops_the_build(monkeypatch):
    monkeypatch.setattr(universal, "SPLIT_WHEELS", {"no-such-dist": "no_such_package_here"})
    with pytest.raises(SystemExit, match="no-such-dist isn't installed"):
        universal.make_dependencies_universal()


def test_wheels_are_asked_for_the_oldest_macos_the_app_supports():
    spec = (ROOT / "packaging" / "snipforge.spec").read_text(encoding="utf-8")
    assert f'"LSMinimumSystemVersion": "{universal.MIN_MACOS.replace("_", ".")}"' in spec


@pytest.mark.mac
def test_lipo_reads_architectures_on_a_mac():
    found = universal.lipo_archs(Path(sys.executable).resolve())
    assert found and found <= {"arm64", "arm64e", "x86_64"}
