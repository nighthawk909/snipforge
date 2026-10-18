"""Repeat last snip: the same area of the same monitor again, straight away, with no overlay."""
import pytest
from PySide6.QtCore import QEvent, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QMouseEvent, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snipforge import autostart, prefs
from snipforge.library import Library
from snipforge.settings import app_settings

NAMES = ("capture_last_area", "capture_delay", "capture_pointer", "capture_mode")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    for name in NAMES:
        app_settings().remove(prefs.KEYS[name])
    yield
    for name in NAMES:
        app_settings().remove(prefs.KEYS[name])


def coded(screen) -> QPixmap:
    """The screen, with each 10 x 10 block a different color, so a snip shows where it came from."""
    dpr = screen.devicePixelRatio()
    pix = QPixmap(round(screen.geometry().width() * dpr), round(screen.geometry().height() * dpr))
    pix.fill(Qt.white)
    p = QPainter(pix)
    for y in range(0, pix.height(), 10):
        for x in range(0, min(pix.width(), 2550), 10):
            p.fillRect(x, y, 10, 10, QColor(x // 10, (y // 10) % 256, 128))
    p.end()
    pix.setDevicePixelRatio(dpr)
    return pix


@pytest.fixture
def screens(monkeypatch):
    from snipforge import capture
    monkeypatch.setattr(capture, "_grab_screen", coded)
    monkeypatch.setattr(capture.pointer, "grab", lambda: None)
    return QGuiApplication.primaryScreen()


def run(session):
    session.start()
    for _ in range(50):
        if session._finished:
            break
        QTest.qWait(10)


# --- the remembered area -------------------------------------------------------------------------

def test_the_last_area_is_remembered_and_validated():
    assert prefs.last_area() is None
    prefs.set_last_area("\\\\.\\DISPLAY2", 10, 20, 300, 200)
    assert prefs.last_area() == ("\\\\.\\DISPLAY2", (10, 20, 300, 200))
    for junk in ("nope", "{}", '{"screen": "x", "rect": [1, 2, 0, 5]}', '{"screen": 3, "rect": [1, 2, 3, 4]}',
                 '{"screen": "x", "rect": [1, 2, "a", 4]}'):
        app_settings().setValue(prefs.KEYS["capture_last_area"], junk)
        assert prefs.last_area() is None, junk
    with pytest.raises(ValueError):
        prefs.set_last_area("x", 0, 0, 0, 10)


# --- the session -----------------------------------------------------------------------------------

def test_a_snip_remembers_its_area(qapp, screens):
    from snipforge.capture import CaptureSession
    results = []
    session = CaptureSession(results.append)
    session.start()
    overlay = session.focus
    pt = QPointF(30, 40)
    for etype, at in ((QEvent.MouseButtonPress, pt), (QEvent.MouseMove, QPointF(130, 90)),
                      (QEvent.MouseButtonRelease, QPointF(130, 90))):
        buttons = Qt.NoButton if etype == QEvent.MouseButtonRelease else Qt.LeftButton
        QApplication.sendEvent(overlay, QMouseEvent(etype, at, overlay.mapToGlobal(at), Qt.LeftButton, buttons,
                                                    Qt.NoModifier))
    dpr = overlay._dpr
    assert session.area == (screens.name(), QRect(round(30 * dpr), round(40 * dpr), round(100 * dpr), round(50 * dpr)))
    assert results[0].size() == session.area[1].size()


def test_repeat_takes_the_same_area_with_no_overlay(qapp, screens):
    from snipforge.capture import CaptureSession
    area = (screens.name(), QRect(20, 30, 300, 200))
    results = []
    session = CaptureSession(results.append, repeat=area)
    shown = []
    session._show_overlays = lambda: shown.append(1)
    run(session)
    assert shown == [] and session.area == area
    assert results[0].size().toTuple() == (300, 200)
    assert results[0].pixelColor(0, 0) == QColor(2, 3, 128)  # the block at (20, 30)


def test_repeat_honours_the_delay(qapp, screens, monkeypatch):
    from snipforge import countdown
    from snipforge.capture import CaptureSession
    monkeypatch.setattr(countdown, "SETTLE_MS", 0)
    prefs.set_capture_delay(3)
    results = []
    session = CaptureSession(results.append, repeat=(screens.name(), QRect(0, 0, 50, 50)))
    session.start()
    assert session.countdown is not None and results == []
    session.snip_now()
    for _ in range(50):
        if results:
            break
        QTest.qWait(10)
    assert results[0].size().toTuple() == (50, 50)


def test_repeat_draws_the_pointer_when_it_is_included(qapp, screens, monkeypatch):
    from snipforge import capture
    prefs.set_capture_pointer(True)
    monkeypatch.setattr(capture.pointer, "grab", lambda: "the pointer")

    def composite(image, shot, origin):
        assert shot == "the pointer"
        marked = image.copy()
        marked.fill(QColor("#ff00ff"))
        return marked

    monkeypatch.setattr(capture.pointer, "composite", composite)
    results = []
    run(capture.CaptureSession(results.append, repeat=(screens.name(), QRect(0, 0, 40, 40))))
    assert results[0].pixelColor(5, 5) == QColor("#ff00ff")


def test_an_area_no_longer_on_screen_falls_back_to_a_normal_snip(qapp, screens):
    from snipforge.capture import CaptureSession
    for area in (("no such monitor", QRect(0, 0, 50, 50)), (screens.name(), QRect(-10, 0, 50, 50)),
                 (screens.name(), QRect(0, 0, 99999, 50))):
        results = []
        session = CaptureSession(results.append, repeat=area)
        session.start()
        try:
            assert session._overlays and results == []
            assert "isn't on screen" in session.note
        finally:
            session._finish(None)


def test_scrolling_snips_are_not_repeated(qapp):
    from snipforge.capture import CaptureSession
    session = CaptureSession(lambda image: None)
    session._scroll_done(None, None)
    assert session.area is None


# --- the app -------------------------------------------------------------------------------------

@pytest.fixture
def forge(qapp, tmp_path, monkeypatch, no_dialogs):
    from snipforge import app as app_module
    monkeypatch.setattr(app_module.HotkeyListener, "start", lambda self: True)
    monkeypatch.setattr(app_module.HotkeyListener, "stop", lambda self: None)
    monkeypatch.setattr(app_module.UpdateFlow, "start", lambda self: None)
    f = app_module.SnipForgeApp(qapp, Library(tmp_path))
    f.indexer._recognize = lambda image: ""
    f.messages = []
    f._tray.showMessage = lambda *args: f.messages.append(args[1])
    yield f
    for window in list(f._windows):
        window.close()
    f.indexer.close()


def test_the_app_remembers_each_snips_area_for_repeat(forge, white_image):
    assert not forge._repeat_action.isEnabled()

    class Session:
        source = None
        note = None
        area = ("\\\\.\\DISPLAY1", QRect(5, 6, 70, 80))

    forge._session = Session()
    forge._on_captured(white_image)
    assert prefs.last_area() == ("\\\\.\\DISPLAY1", (5, 6, 70, 80)) and forge._repeat_action.isEnabled()


def test_repeat_with_nothing_to_repeat_says_so_and_snips(forge, monkeypatch):
    started = []
    monkeypatch.setattr(forge, "capture", lambda: started.append("snip"))
    forge.repeat_last()
    assert started == ["snip"] and "Nothing to repeat yet" in forge.messages[0]


def test_repeat_starts_a_repeat_session(forge, monkeypatch):
    from snipforge import app as app_module
    screen = QGuiApplication.primaryScreen()
    prefs.set_last_area(screen.name(), 1, 2, 30, 40)
    made = []

    class Session:
        def __init__(self, on_done, repeat=None):
            made.append(repeat)

        def start(self):
            pass

    monkeypatch.setattr(app_module, "CaptureSession", Session)
    forge.repeat_last()
    assert made == [(screen.name(), QRect(1, 2, 30, 40))] and forge._session is not None


def test_repeat_of_an_area_that_is_gone_says_so_and_snips(forge, monkeypatch):
    prefs.set_last_area("no such monitor", 1, 2, 30, 40)
    started = []
    monkeypatch.setattr(forge, "capture", lambda: started.append("snip"))
    forge.repeat_last()
    assert started == ["snip"] and "isn't on screen" in forge.messages[0]


def test_shortcuts_and_commands_reach_their_actions(forge, monkeypatch):
    calls = []
    monkeypatch.setattr(forge, "capture", lambda: calls.append("snip"))
    monkeypatch.setattr(forge, "repeat_last", lambda: calls.append("repeat"))
    monkeypatch.setattr(forge, "show_home", lambda: calls.append("library"))
    for action in ("snip", "repeat", "library"):
        forge._on_shortcut(action)
    forge.handle_command("repeat")
    assert calls == ["snip", "repeat", "library", "repeat"]


def test_repeat_from_the_command_line():
    from snipforge.app import _command_from_args
    assert _command_from_args(["SnipForge.exe", "--repeat"]) == "repeat"


def test_saved_shortcuts_that_cannot_be_registered_are_reported(qapp, tmp_path, monkeypatch, no_dialogs):
    from snipforge import app as app_module
    from snipforge import shortcuts
    monkeypatch.setattr(app_module.HotkeyListener, "start", lambda self: True)
    monkeypatch.setattr(app_module.HotkeyListener, "stop", lambda self: None)
    monkeypatch.setattr(app_module.UpdateFlow, "start", lambda self: None)
    monkeypatch.setattr(shortcuts.GlobalShortcuts, "apply_saved",
                        lambda self: {"library": "Another app already uses Ctrl+Alt+L."})
    shown = []
    monkeypatch.setattr(app_module.QSystemTrayIcon, "showMessage", lambda self, *args: shown.append(args[1]))
    f = app_module.SnipForgeApp(qapp, Library(tmp_path))
    try:
        assert any("Ctrl+Alt+L" in text for text in shown)
    finally:
        f.indexer.close()
        f._shortcuts.close()
