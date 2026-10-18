"""Repeat last snip: the same area of the same monitor again, straight away, with no overlay."""
import pytest
from PySide6.QtCore import QEvent, QObject, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeyEvent, QMouseEvent, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snipforge import autostart, prefs
from snipforge.library import Library
from snipforge.prefs import LastArea
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
def screen(monkeypatch):
    from snipforge import capture
    monkeypatch.setattr(capture, "_grab_screen", coded)
    monkeypatch.setattr(capture.pointer, "grab", lambda: None)
    return QGuiApplication.primaryScreen()


def area(screen, x, y, w, h) -> LastArea:
    from snipforge.capture import area_on
    return area_on(screen, QRect(x, y, w, h))


def run(session):
    session.start()
    for _ in range(50):
        if session._finished:
            break
        QTest.qWait(10)


# --- the remembered area -------------------------------------------------------------------------

def test_the_last_area_is_remembered_and_validated():
    assert prefs.last_area() is None
    remembered = LastArea("Sceptre O34", (1920, 0, 2560, 1440), 1.25, (10, 20, 300, 200))
    prefs.set_last_area(remembered)
    assert prefs.last_area() == remembered
    for junk in ("nope", "{}", '{"screen": "x", "rect": [1, 2, 3, 4]}',  # an older format: no monitor details
                 '{"screen": "x", "geometry": [0, 0, 10, 10], "scale": 1, "rect": [1, 2, 0, 5]}',
                 '{"screen": 3, "geometry": [0, 0, 10, 10], "scale": 1, "rect": [1, 2, 3, 4]}',
                 '{"screen": "x", "geometry": [0, 0, 10, 10], "scale": 99, "rect": [1, 2, 3, 4]}',
                 '{"screen": "x", "geometry": [0, 0, 10, 10], "scale": 1, "rect": [1, 2, "a", 4]}',
                 '{"screen": "x", "geometry": [0, 0, 10, 10], "scale": 1, "rect": [1, 2, 99999999999, 4]}'):
        app_settings().setValue(prefs.KEYS["capture_last_area"], junk)
        assert prefs.last_area() is None, junk
    with pytest.raises(ValueError):
        prefs.set_last_area(LastArea("x", (0, 0, 10, 10), 1.0, (0, 0, 0, 10)))


# --- the session -----------------------------------------------------------------------------------

def test_a_snip_remembers_its_area_and_monitor(qapp, screen):
    from snipforge.capture import CaptureSession
    results = []
    session = CaptureSession(results.append)
    session.start()
    overlay = session.focus
    for etype, at in ((QEvent.MouseButtonPress, QPointF(30, 40)), (QEvent.MouseMove, QPointF(130, 90)),
                      (QEvent.MouseButtonRelease, QPointF(130, 90))):
        buttons = Qt.NoButton if etype == QEvent.MouseButtonRelease else Qt.LeftButton
        QApplication.sendEvent(overlay, QMouseEvent(etype, at, overlay.mapToGlobal(at), Qt.LeftButton, buttons,
                                                    Qt.NoModifier))
    dpr = overlay._dpr
    g = screen.geometry()
    assert session.area == LastArea(screen.name(), (g.x(), g.y(), g.width(), g.height()),
                                    round(screen.devicePixelRatio(), 3),
                                    (round(30 * dpr), round(40 * dpr), round(100 * dpr), round(50 * dpr)))
    assert results[0].size().toTuple() == session.area.rect[2:]


def test_repeat_takes_the_same_area_with_no_overlay(qapp, screen):
    from snipforge.capture import CaptureSession
    last = area(screen, 20, 30, 300, 200)
    results = []
    session = CaptureSession(results.append, repeat=last)
    shown = []
    session._show_overlays = lambda: shown.append(1)
    run(session)
    assert shown == [] and session.area == last
    assert results[0].size().toTuple() == (300, 200)
    assert results[0].pixelColor(0, 0) == QColor(2, 3, 128)  # the block at (20, 30)


def test_repeat_honours_the_delay(qapp, screen, monkeypatch):
    from snipforge import countdown
    from snipforge.capture import CaptureSession
    monkeypatch.setattr(countdown, "SETTLE_MS", 0)
    prefs.set_capture_delay(3)
    results = []
    session = CaptureSession(results.append, repeat=area(screen, 0, 0, 50, 50))
    session.start()
    assert session.countdown is not None and results == []
    session.snip_now()
    for _ in range(50):
        if results:
            break
        QTest.qWait(10)
    assert results[0].size().toTuple() == (50, 50)


def test_repeat_draws_the_pointer_when_it_is_included(qapp, screen, monkeypatch):
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
    run(capture.CaptureSession(results.append, repeat=area(screen, 0, 0, 40, 40)))
    assert results[0].pixelColor(5, 5) == QColor("#ff00ff")


def test_an_area_no_longer_on_screen_gets_a_normal_snip_and_says_so_at_once(qapp, screen):
    from snipforge.capture import CaptureSession, on_screen
    g = screen.geometry()
    here = (g.x(), g.y(), g.width(), g.height())
    scale = round(screen.devicePixelRatio(), 3)
    gone = [
        LastArea("no such monitor", here, scale, (0, 0, 50, 50)),
        LastArea(screen.name(), (g.x() + 5000, g.y(), g.width(), g.height()), scale, (0, 0, 50, 50)),  # moved
        LastArea(screen.name(), (g.x(), g.y(), g.width() + 640, g.height()), scale, (0, 0, 50, 50)),  # resized
        LastArea(screen.name(), here, scale + 0.25, (0, 0, 50, 50)),  # scaling changed
        LastArea(screen.name(), here, scale, (-10, 0, 50, 50)),
        LastArea(screen.name(), here, scale, (0, 0, 99999, 50)),
    ]
    for last in gone:
        assert not on_screen(last), last
        notes, results = [], []
        session = CaptureSession(results.append, repeat=last, on_note=notes.append)
        session.start()
        try:
            assert session._overlays and results == [], last
            assert len(notes) == 1 and "isn't on screen" in notes[0] and session.note is None
        finally:
            session._finish(None)
    assert on_screen(area(screen, 0, 0, 50, 50))


def test_identical_monitors_are_told_apart_by_where_they_are(qapp, monkeypatch):
    from snipforge import capture

    class Monitor:
        def __init__(self, x):
            self.x = x

        def name(self):
            return "Generic PnP Monitor"

        def geometry(self):
            return QRect(self.x, 0, 1920, 1080)

        def devicePixelRatio(self):  # noqa: N802 - Qt API
            return 1.0

    left, right = Monitor(0), Monitor(1920)
    monkeypatch.setattr(capture.QGuiApplication, "screens", staticmethod(lambda: [left, right]))
    assert capture.screen_for(capture.area_on(right, QRect(5, 5, 50, 50))) is right
    assert capture.screen_for(capture.area_on(left, QRect(5, 5, 50, 50))) is left
    twin = Monitor(1920)  # can't tell two exact matches apart: don't guess
    monkeypatch.setattr(capture.QGuiApplication, "screens", staticmethod(lambda: [left, right, twin]))
    assert capture.screen_for(capture.area_on(right, QRect(5, 5, 50, 50))) is None


def test_scrolling_snips_never_change_the_area_to_repeat(qapp, screen, monkeypatch):
    from snipforge import capture

    class Scrolled(QObject):
        finished = Signal(object)

        def __init__(self, *_args, **_kwargs):
            super().__init__()

        def start(self):
            image = QImage(100, 900, QImage.Format_RGB32)
            image.fill(Qt.white)
            self.finished.emit(image)

        def cancel(self):
            pass

    monkeypatch.setattr(capture.scroll_input, "available", lambda: True)
    monkeypatch.setattr(capture, "ScrollCapture", Scrolled)
    results = []
    session = capture.CaptureSession(results.append)
    session.start()
    overlay = session.focus
    QApplication.sendEvent(overlay, QKeyEvent(QEvent.KeyPress, Qt.Key_S, Qt.NoModifier, "s"))
    QApplication.sendEvent(overlay, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    assert results and results[0].height() == 900
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
    f._shortcuts.close()


def test_the_app_remembers_each_snips_area_for_repeat(forge, white_image):
    assert not forge._repeat_action.isEnabled()
    last = LastArea("Sceptre O34", (0, 0, 3440, 1440), 1.0, (5, 6, 70, 80))

    class Session:
        source = None
        note = None
        area = last

    forge._session = Session()
    forge._on_captured(white_image)
    assert prefs.last_area() == last and forge._repeat_action.isEnabled()


def test_repeat_with_nothing_to_repeat_says_so_and_snips(forge, monkeypatch):
    started = []
    monkeypatch.setattr(forge, "capture", lambda: started.append("snip"))
    forge.repeat_last()
    assert started == ["snip"] and "Nothing to repeat yet" in forge.messages[0]


def test_repeat_starts_a_repeat_session(forge, monkeypatch):
    from snipforge import app as app_module
    last = area(QGuiApplication.primaryScreen(), 1, 2, 30, 40)
    prefs.set_last_area(last)
    made = []

    class Session:
        def __init__(self, on_done, repeat=None, on_note=None):
            made.append(repeat)
            on_note("a note")

        def start(self):
            pass

    monkeypatch.setattr(app_module, "CaptureSession", Session)
    forge.repeat_last()
    assert made == [last] and forge._session is not None and forge.messages == ["a note"]


def test_repeat_of_an_area_that_is_gone_says_so_and_snips(forge, monkeypatch):
    prefs.set_last_area(LastArea("no such monitor", (0, 0, 800, 600), 1.0, (1, 2, 30, 40)))
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
                        lambda self: {"library": "Ctrl+Alt+F10: Another app already uses Ctrl+Alt+F10."})
    shown = []
    monkeypatch.setattr(app_module.QSystemTrayIcon, "showMessage", lambda self, *args: shown.append(args[1]))
    f = app_module.SnipForgeApp(qapp, Library(tmp_path))
    try:
        assert any("Ctrl+Alt+F10" in text and "Settings > Capture" in text for text in shown)
    finally:
        f.indexer.close()
        f._shortcuts.close()
