"""After a silent update the installer starts SnipForge again the way SnipForge asked (/RELAUNCH=...).

Compiles a renamed copy of the real packaging/installer.iss (its own AppId and name, so it can't touch
an installed SnipForge) around a stand-in SnipForge.exe, runs it silently and reads Inno Setup's log
for what it started. Needs Inno Setup 6, which the CI runners have.
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
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"


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
    app_id = "{{" + str(uuid.uuid4()).upper() + "}"
    text = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    assert REAL_APP_ID in text
    text = (text.replace(REAL_APP_ID, app_id)
            .replace('#define AppName "SnipForge"', f'#define AppName "{TEST_NAME}"')
            .replace("Compression=lzma2/max", "Compression=none")
            .replace("=..\\", f"={ROOT}\\"))
    assert REAL_APP_ID not in text and f'"{TEST_NAME}"' in text
    script = work / "relaunch-test.iss"
    script.write_text(text, encoding="utf-8")
    proc = subprocess.run([str(iscc), "/Q", f"/DSourceDir={app}", f"/O{work}", str(script)],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    yield next(work.glob(f"{TEST_NAME}-*-setup.exe")), app_id
    _remove_test_install(app_id)


def _remove_test_install(app_id: str) -> None:
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"{UNINSTALL_KEY}\{app_id}_is1")
    except OSError:
        pass
    start_menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    (start_menu / f"{TEST_NAME}.lnk").unlink(missing_ok=True)


def started_with(setup: Path, target: Path, reopen: str) -> list[str]:
    """Install silently as SnipForge's updater would; the parameters of each program Setup started."""
    log = target.parent / f"{target.name}.log"
    args = [str(setup), *update_platform.INSTALLER_ARGS, *update_platform.RELAUNCH_ARGS[reopen],
            "/TASKS=", f"/DIR={target}", f"/LOG={log}"]  # no tasks: no startup entry or desktop icon
    proc = subprocess.run(args, capture_output=True, timeout=300)
    assert proc.returncode == 0, log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    text = log.read_text(encoding="utf-8", errors="replace")
    started = []
    for run in text.split("-- Run entry --")[1:]:
        parameters = re.search(r"Parameters: (.*)", run)  # not logged when there are none
        started.append(parameters[1].strip() if parameters else "")
    return started


@pytest.mark.parametrize("reopen, expected", [
    (update_platform.REOPEN_WINDOW, [""]),             # on screen: the Library opens
    (update_platform.REOPEN_TRAY, ["--background"]),   # how older SnipForge versions ask
    (update_platform.REOPEN_NONE, []),                 # installed as SnipForge quit
])
def test_a_silent_update_starts_snipforge_again_as_asked(setup_exe, tmp_path, reopen, expected):
    setup, _app_id = setup_exe
    assert started_with(setup, tmp_path / "installed", reopen) == expected
