"""Desktop tests: exercise the real Windows integrations, not stand-ins.

These open real windows (and briefly the Windows share sheet), so they don't run with the
regular suite. Run them with:

    QT_QPA_PLATFORM=windows python -m pytest -m desktop

CI runs them on every change; run them locally before a release.
"""
import tempfile
import time
import winreg
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtWidgets import QApplication, QWidget

from snipforge import share

pytestmark = pytest.mark.desktop


@pytest.fixture(scope="module", autouse=True)
def real_platform(qapp):
    if QGuiApplication.platformName() == "offscreen":
        pytest.skip("desktop tests need a real display: set QT_QPA_PLATFORM=windows")


@pytest.fixture
def window(qapp):
    w = QWidget()
    w.setWindowTitle("SnipForge desktop test")
    w.resize(320, 200)
    w.show()
    QApplication.processEvents()
    yield w
    w.close()


@pytest.fixture
def png(qapp):
    image = QImage(64, 64, QImage.Format_ARGB32)
    image.fill(QColor("#3b82f6"))
    return share.export(image, Path(tempfile.mkdtemp(prefix="snipforge-desktop-")))


def pump(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.02)


def test_windows_share_sheet_opens_from_our_window(window, png):
    """The 'Invalid window handle' bug: the real share command must accept our window."""
    if not share.has_shell_verb(png, share.WINDOWS_SHARE_VERB):
        pytest.skip("this Windows edition has no share sheet")
    assert share.invoke_shell_verb(png, share.WINDOWS_SHARE_VERB, int(window.winId()))
    pump(1.5)  # closing the owner window afterwards dismisses the sheet


def test_keyboard_hook_installs(qapp):
    from snipforge.hotkey import HotkeyListener
    listener = HotkeyListener()
    try:
        assert listener.start(), "Win+Shift+S hook could not be installed"
        listener.refresh()  # the periodic re-hook must not break it
        pump(0.3)
    finally:
        listener.stop()


def test_teams_link_handler_is_registered():
    try:
        winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "msteams").Close()
    except OSError:
        pytest.skip("Microsoft Teams isn't installed")


def test_onedrive_commands_are_found_without_uploading():
    accounts = share.onedrive_accounts()
    if not accounts:
        pytest.skip("no OneDrive account signed in")
    existing = next((p for p in accounts[0].folder.iterdir() if p.is_file()), None)
    if existing is None:
        pytest.skip("OneDrive folder has no files to inspect")
    assert share.has_shell_verb(existing, share.ONEDRIVE_COPY_LINK_VERB)


def test_clipboard_round_trip(qapp):
    image = QImage(40, 30, QImage.Format_ARGB32)
    image.fill(QColor("#ff3b30"))
    QGuiApplication.clipboard().setImage(image)
    pump(0.2)
    assert QGuiApplication.clipboard().image().size() == image.size()


def test_update_policy_is_read_from_the_real_registry():
    """Policy values of each registry type come through winreg as IT would set them (on a test key)."""
    import os

    from snipforge import update_platform, update_policy

    parent, key_path = r"Software\SnipForgeDesktopTest", r"Software\SnipForgeDesktopTest\Policies"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, "UpdateFeed", 0, winreg.REG_EXPAND_SZ, r"%TEMP%\SnipForgeFeed")
        winreg.SetValueEx(key, "UpdateMode", 0, winreg.REG_SZ, "Auto")
        winreg.SetValueEx(key, "AllowRollback", 0, winreg.REG_DWORD, 0)
        winreg.SetValueEx(key, "DisableUpdateCheck", 0, winreg.REG_DWORD, 0)
    try:
        hklm, hkcu = update_platform.read_policy_hives(key_path)
        assert hklm == {} and hkcu["AllowRollback"] == 0
        policy = update_policy.parse_policy([hklm, hkcu])
        assert policy.source == "feed" and policy.feed == os.path.expandvars(r"%TEMP%\SnipForgeFeed")
        assert policy.mode == "auto" and not policy.allow_rollback and not policy.disabled
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, parent)


def test_update_engine_can_tell_windows_is_not_signing_out():
    """The real GetSystemMetrics(SM_SHUTTINGDOWN) call works, so an install on quit isn't blocked by mistake."""
    from snipforge import update_platform

    assert update_platform.session_ending() is False
