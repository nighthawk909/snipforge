"""After a silent update the installer starts SnipForge again the way SnipForge asked (/RELAUNCH=...).

Compiles a copy of the real packaging/installer.iss around a stand-in SnipForge.exe, runs it silently
and reads Inno Setup's log for what it started. The copy can't touch an installed SnipForge: its own
AppId and name, no uninstaller (so no Apps entry, and nothing that could later run the real script's
uninstall steps), no shortcuts, no registry entries and no tasks. Needs Inno Setup 6 (CI has it).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from snipforge import update_platform
from tests.test_code_signing import build

pytestmark = pytest.mark.windows

ROOT = Path(__file__).resolve().parents[1]
REAL_APP_ID = "{{23E25F09-747F-4B3D-B9D0-E81D15E709B3}"
TEST_NAME = "SnipForge Relaunch Test"


def without_section(text: str, name: str) -> str:
    """The script without its [name] section."""
    start = text.index(f"\n[{name}]\n")
    end = text.index("\n[", start + 1)
    return text[:start] + text[end:]


def safe_copy(text: str) -> str:
    app_id = "{{" + str(uuid.uuid4()).upper() + "}"
    assert REAL_APP_ID in text
    text = (text.replace(REAL_APP_ID, app_id)
            .replace('#define AppName "SnipForge"', f'#define AppName "{TEST_NAME}"')
            .replace('#define AppUserModelID "SnipForge.SnipForge"', '#define AppUserModelID "SnipForge.RelaunchTest"')
            .replace("Compression=lzma2/max", "Compression=none\nUninstallable=no")
            .replace("=..\\", f"={ROOT}\\"))
    for section in ("Icons", "Registry", "UninstallRun", "UninstallDelete"):
        text = without_section(text, section)
    assert REAL_APP_ID not in text and "SnipForge.SnipForge" not in text and "Uninstallable=no" in text
    assert "HKCU" not in text and "HKA" not in text and "{autoprograms}" not in text
    return text


@pytest.fixture(scope="module")
def setup_exe(tmp_path_factory):
    iscc = next((c for c in build.ISCC_CANDIDATES if c.exists()), None) or shutil.which("iscc")
    if iscc is None:
        (pytest.fail if os.environ.get("CI") else pytest.skip)("needs Inno Setup 6 (the CI runners have it)")
    work = tmp_path_factory.mktemp("relaunch")
    app = work / "app"
    app.mkdir()
    # whoami ignores what it's started with and exits at once: the log says how it was started
    shutil.copy(Path(os.environ["SystemRoot"]) / "System32" / "whoami.exe", app / "SnipForge.exe")
    script = work / "relaunch-test.iss"
    script.write_text(safe_copy((ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")),
                      encoding="utf-8")
    proc = subprocess.run([str(iscc), "/Q", f"/DSourceDir={app}", f"/O{work}", str(script)],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return next(work.glob(f"{TEST_NAME}-*-setup.exe"))


def started_with(setup: Path, target: Path, reopen: str) -> list[str]:
    """Install silently as SnipForge's updater would; the parameters of each program Setup started."""
    log = target.parent / f"{target.name}.log"
    args = [str(setup), *update_platform.INSTALLER_ARGS, *update_platform.RELAUNCH_ARGS[reopen],
            "/MERGETASKS=!desktopicon,!autostart", f"/DIR={target}", f"/LOG={log}"]
    proc = subprocess.run(args, capture_output=True, timeout=300)
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    assert proc.returncode == 0, text
    started = []
    for run in text.split("-- Run entry --")[1:]:
        assert re.search(r"Filename: .*\\SnipForge\.exe", run), run
        parameters = re.search(r"Parameters: (.*)", run)  # not logged when there are none
        started.append(parameters[1].strip() if parameters else "")
    return started


@pytest.mark.parametrize("reopen, expected", [
    (update_platform.REOPEN_WINDOW, [""]),             # on screen: the Library opens
    (update_platform.REOPEN_TRAY, ["--background"]),   # how older SnipForge versions ask
    (update_platform.REOPEN_NONE, []),                 # installed as SnipForge quit
])
def test_a_silent_update_starts_snipforge_again_as_asked(setup_exe, tmp_path, reopen, expected):
    assert started_with(setup_exe, tmp_path / "installed", reopen) == expected


def test_the_copy_under_test_cannot_touch_a_real_install():
    text = safe_copy((ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8"))
    assert "[Run]" in text and "RelaunchAfterSilentInstall('window')" in text  # what's tested stays
