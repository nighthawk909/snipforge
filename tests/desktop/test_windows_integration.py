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


def test_title_bar_follows_the_theme(window):
    """Windows 11 accepts SnipForge's dark/light and caption colors for a real window."""
    import ctypes

    from snipforge.theme import titlebar

    hwnd = int(window.winId())
    assert titlebar.style_title_bar(hwnd, True, "#211359", "#FFFFFF") is True
    value = ctypes.c_int(0)
    result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        ctypes.c_void_p(hwnd), ctypes.c_uint(titlebar.DWMWA_USE_IMMERSIVE_DARK_MODE),
        ctypes.byref(value), ctypes.sizeof(value))
    assert result == 0 and value.value == 1
    assert titlebar.style_title_bar(hwnd, False, "#F3F3F5", "#1B1B1F") is True


def test_text_recognition_works_on_the_indexer_thread(qapp, tmp_path):
    """Library search reads snips with the real Windows OCR, off the UI thread."""
    from PySide6.QtGui import QFont, QPainter

    from snipforge import ocr
    from snipforge.indexer import TextIndexer
    from snipforge.library import Library

    try:
        ocr.recognize(QImage(40, 20, QImage.Format_ARGB32))
    except ocr.OcrUnavailable as exc:
        pytest.skip(str(exc))
    image = QImage(640, 90, QImage.Format_ARGB32)
    image.fill(QColor("white"))
    p = QPainter(image)
    p.setFont(QFont("Segoe UI", 20))
    p.setPen(QColor("black"))
    p.drawText(16, 58, "Quarterly margin review")
    p.end()
    lib = Library(tmp_path)
    entry = lib.create(image)
    indexer = TextIndexer(lib)
    assert indexer.index(entry)
    deadline = time.time() + 20
    while not lib.meta(entry).indexed and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.05)
    indexer.close()
    assert "margin" in lib.meta(entry).text.lower()


def test_app_names_come_from_real_processes():
    """OpenProcess + QueryFullProcessImageNameW + version info work for a real process (this one)."""
    import os
    import sys

    from snipforge import winrects

    path = winrects._process_path(os.getpid())
    assert os.path.normcase(path) == os.path.normcase(sys.executable)
    assert winrects._app_name(path)  # 'Python' or its file description
    assert winrects._app_name("C:/Program Files/Microsoft Office/root/Office16/EXCEL.EXE") == "Excel"
    for info in winrects.window_infos():
        assert info.rect is not None and isinstance(info.app, str)
