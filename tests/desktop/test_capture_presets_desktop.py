"""Desktop tests for capture presets: a preset's shortcut registered with Windows' own RegisterHotKey,
and a Copy preset putting its finished snip on the real clipboard.

    QT_QPA_PLATFORM=windows python -m pytest -m desktop tests/desktop/test_capture_presets_desktop.py

No keys are pressed: the hotkey's message is posted to SnipForge's own thread, as Windows posts it.
"""
import ctypes
import time
from ctypes import wintypes

import pytest
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtWidgets import QApplication

from snipforge import presets, shortcuts
from snipforge.presets import Preset

pytestmark = pytest.mark.desktop
COMBINATION = "Ctrl+Alt+Shift+F23"  # nobody's: no keyboard has an F23 key


@pytest.fixture(scope="module", autouse=True)
def real_platform(qapp):
    if QGuiApplication.platformName() == "offscreen":
        pytest.skip("desktop tests need a real display: set QT_QPA_PLATFORM=windows")


def pump(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)


def test_a_preset_shortcut_reaches_snipforge_through_windows(qapp):
    keys = shortcuts.GlobalShortcuts()
    fired = []
    keys.triggered.connect(fired.append)
    preset = Preset("dsk1", "Desktop test", shortcut=COMBINATION)
    try:
        assert keys.apply_presets([preset]) == {}
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        thread = ctypes.WinDLL("kernel32").GetCurrentThreadId()
        assert user32.PostThreadMessageW(thread, shortcuts.WM_HOTKEY, keys._id(preset.action), 0)
        pump(0.3)
        assert fired == [preset.action]
    finally:
        keys.close()


def test_a_copy_preset_puts_the_finished_snip_on_the_clipboard(qapp, tmp_path, monkeypatch):
    from snipforge import app as app_module
    from snipforge.library import Library

    monkeypatch.setattr(app_module.HotkeyListener, "start", lambda self: True)
    monkeypatch.setattr(app_module.HotkeyListener, "stop", lambda self: None)
    monkeypatch.setattr(app_module.UpdateFlow, "start", lambda self: None)
    forge = app_module.SnipForgeApp(qapp, Library(tmp_path))
    try:
        forge._note = lambda text: None

        class Taken:
            preset = Preset("dsk2", "Copy test", border=8, beautify="off", destination="copy")
            source = note = area = None

        image = QImage(120, 80, QImage.Format_ARGB32)
        image.fill(QColor("#3366cc"))
        forge._session = Taken()
        forge._on_captured(image)
        pump(0.2)
        copied = QGuiApplication.clipboard().image()
        assert copied.size().toTuple() == (120, 80)
        assert copied.pixelColor(2, 40).name() == "#1c1c1e"  # the thick border, from the real clipboard
    finally:
        forge.indexer.close()
        forge._shortcuts.close()
        presets.save([])
