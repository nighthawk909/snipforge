"""Tests for the Windows-facing pieces: capture overlay, tray app, library window, pin,
single-instance handoff, window detection and the packaged self-test."""
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QKeyEvent, QMouseEvent, QPixmap, QWheelEvent
from PySide6.QtWidgets import QApplication

from snipforge.library import Library


def mouse(widget, etype, pos, button=Qt.LeftButton, buttons=Qt.LeftButton):
    pt = QPointF(*pos)
    QApplication.sendEvent(widget, QMouseEvent(etype, pt, widget.mapToGlobal(pt), button, buttons, Qt.NoModifier))


# --- capture overlay ------------------------------------------------------------------
@pytest.fixture
def overlay(qapp):
    from snipforge.capture import _ScreenOverlay
    screen = QGuiApplication.primaryScreen()
    pix = QPixmap(screen.geometry().size())
    pix.fill(QColor("#e8eaee"))
    o = _ScreenOverlay(screen, pix, [QRect(100, 80, 300, 200)])
    o.resize(screen.geometry().size())
    o.got = []
    o.selected.connect(o.got.append)
    o.cancelled.connect(lambda: o.got.append("cancel"))
    return o


def test_overlay_drag_selects_region(overlay):
    mouse(overlay, QEvent.MouseButtonPress, (10, 10))
    mouse(overlay, QEvent.MouseMove, (60, 40), Qt.NoButton)
    overlay.grab()  # paints selection, size label and magnifier
    mouse(overlay, QEvent.MouseButtonRelease, (110, 70), buttons=Qt.NoButton)
    assert overlay.got[0].size().toTuple() == (100, 60)


def test_overlay_click_takes_hovered_window(overlay):
    mouse(overlay, QEvent.MouseMove, (200, 150), Qt.NoButton, Qt.NoButton)
    overlay.grab()  # paints the hover highlight and hint
    mouse(overlay, QEvent.MouseButtonPress, (200, 150))
    mouse(overlay, QEvent.MouseButtonRelease, (200, 150), buttons=Qt.NoButton)
    assert overlay.got[0].size().toTuple() == (300, 200)


def test_overlay_drag_is_clamped_to_screen(overlay):
    w, h = overlay.width(), overlay.height()
    mouse(overlay, QEvent.MouseButtonPress, (w - 50, h - 40))
    mouse(overlay, QEvent.MouseMove, (w + 300, h + 300), Qt.NoButton)
    mouse(overlay, QEvent.MouseButtonRelease, (w + 300, h + 300), buttons=Qt.NoButton)
    assert overlay.got[0].size().toTuple() == (50, 40)


def test_overlay_remembers_which_window_a_snip_came_from(qapp):
    from snipforge.capture import _ScreenOverlay
    from snipforge.winrects import WindowInfo

    screen = QGuiApplication.primaryScreen()
    pix = QPixmap(screen.geometry().size())
    pix.fill(QColor("#e8eaee"))
    origin = screen.geometry().topLeft()
    excel = WindowInfo(QRect(origin.x() + 100, origin.y() + 80, 300, 200), "Margin review.xlsx - Excel", "Excel")
    o = _ScreenOverlay(screen, pix, [excel.rect], [excel])
    o.resize(screen.geometry().size())
    mouse(o, QEvent.MouseButtonPress, (150, 120))
    mouse(o, QEvent.MouseMove, (200, 160), Qt.NoButton)
    mouse(o, QEvent.MouseButtonRelease, (250, 200), buttons=Qt.NoButton)
    assert o.last_source == excel
    mouse(o, QEvent.MouseButtonPress, (600, 600))
    mouse(o, QEvent.MouseMove, (640, 640), Qt.NoButton)
    mouse(o, QEvent.MouseButtonRelease, (660, 660), buttons=Qt.NoButton)
    assert o.last_source is None  # the desktop, not a window
    o.close()


def test_whole_screen_and_private_windows_are_not_mislabeled(qapp):
    from snipforge.capture import _ScreenOverlay
    from snipforge.winrects import WindowInfo

    screen = QGuiApplication.primaryScreen()
    pix = QPixmap(screen.geometry().size())
    origin = screen.geometry().topLeft()
    mail = WindowInfo(QRect(origin.x() + 200, origin.y() + 150, 400, 300), "Inbox - jane@corp.com - Outlook", "Outlook")
    private = WindowInfo(QRect(origin.x() + 700, origin.y() + 100, 300, 300), "Bank - InPrivate - Edge", "Edge")
    o = _ScreenOverlay(screen, pix, [mail.rect, private.rect], [mail, private])
    o.resize(screen.geometry().size())
    QApplication.sendEvent(o, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))  # whole monitor
    assert o.last_source is None  # the window in the middle didn't take this snip
    mouse(o, QEvent.MouseButtonPress, (750, 150))
    mouse(o, QEvent.MouseMove, (800, 200), Qt.NoButton)
    mouse(o, QEvent.MouseButtonRelease, (900, 300), buttons=Qt.NoButton)
    assert o.last_source.app == "Edge" and o.last_source.title == ""  # private titles are never kept
    o.close()


def test_captured_snips_remember_their_source_and_are_read_for_search(forge, white_image):
    from snipforge.winrects import WindowInfo

    class Session:
        source = WindowInfo(None, "Margin review.xlsx - Excel", "Excel")

    queued = []
    forge.indexer.index = lambda entry, again=False: queued.append((entry.id, again)) or True
    forge._session = Session()
    forge._on_captured(white_image)
    entry = forge.library.entries()[0]
    assert forge.library.meta(entry).app == "Excel" and queued == [(entry.id, False)]
    forge._windows[0].close()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert queued[-1] == (entry.id, True)  # closing the editor reads the edited snip again


def test_overlay_keys_and_right_click(overlay):
    QApplication.sendEvent(overlay, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    assert overlay.got[0].size() == overlay.size()
    QApplication.sendEvent(overlay, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    mouse(overlay, QEvent.MouseButtonPress, (5, 5), Qt.RightButton, Qt.RightButton)
    assert overlay.got[1:] == ["cancel", "cancel"]
    QApplication.sendEvent(overlay, QEvent(QEvent.Leave))


def test_capture_session_cancel(qapp):
    from snipforge.capture import CaptureSession
    results = []
    session = CaptureSession(results.append)
    session.start()
    session._finish(None)
    session._finish(None)  # second finish is ignored
    assert results == [None]


def test_visible_windows_returns_rects():
    from snipforge.winrects import visible_windows
    rects = visible_windows()
    assert all(isinstance(r, QRect) and r.width() >= 40 for r in rects)


# --- tray app ------------------------------------------------------------------------
@pytest.fixture
def forge(qapp, tmp_path, monkeypatch, no_dialogs):
    from snipforge import app as app_module
    monkeypatch.setattr(app_module.HotkeyListener, "start", lambda self: True)
    monkeypatch.setattr(app_module.HotkeyListener, "stop", lambda self: None)
    monkeypatch.setattr(app_module.UpdateFlow, "start", lambda self: None)  # no update checks in unit tests
    f = app_module.SnipForgeApp(qapp, Library(tmp_path))
    f.indexer._recognize = lambda image: "fake words"  # never run real OCR in unit tests
    yield f
    for window in list(f._windows):
        window.close()
    f.indexer.close()


def test_capture_opens_editor_and_library_entry(forge, white_image):
    forge._on_captured(white_image)
    editors = [w for w in forge._windows if type(w).__name__ == "EditorWindow"]
    assert len(editors) == 1 and len(forge.library.entries()) == 1
    assert QGuiApplication.clipboard().image().size() == white_image.size()
    forge._on_captured(None)  # cancelled snip does nothing
    assert len(forge._windows) == 1


def test_open_entry_focuses_existing_editor(forge, white_image):
    forge._on_captured(white_image)
    entry = forge.library.entries()[0]
    forge.open_entry(entry.id)
    assert len(forge._windows) == 1
    forge._windows[0].close()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    forge.open_entry(entry.id)
    assert len(forge._windows) == 1


def test_open_entry_with_corrupt_document_disables_autosave(forge, white_image, no_dialogs):
    entry = forge.library.create(white_image)
    (entry.path / "document.json").write_text("{broken", encoding="utf-8")
    forge.open_entry(entry.id)
    assert no_dialogs and forge._windows[0]._autosave_enabled is False
    forge.open_entry("missing-id")
    assert no_dialogs[-1][0] == "information"


def test_pin_home_commands_and_about(forge, white_image, no_dialogs, monkeypatch):
    forge.pin(white_image)
    assert any(type(w).__name__ == "PinWindow" for w in forge._windows)
    forge.handle_command("home")
    assert forge._home is not None and forge._home.isVisible()
    calls = []
    monkeypatch.setattr(forge, "capture", lambda: calls.append("snip"))
    forge.handle_command("snip")
    assert calls == ["snip"]
    forge.about()
    assert [k for k, _ in no_dialogs] == ["about"]


def test_capture_runs_one_session_at_a_time(forge, monkeypatch):
    from snipforge import app as app_module
    started = []
    monkeypatch.setattr(app_module.CaptureSession, "start", lambda self: started.append(self))
    forge.capture()
    forge.capture()
    QApplication.processEvents()
    assert forge._session is not None


def test_pressing_the_snip_key_during_a_countdown_snips_now(forge):
    calls = []

    class CountingDown:
        def snip_now(self):
            calls.append("now")

    forge._session = CountingDown()
    forge.capture()
    assert calls == ["now"]
    forge._session = None


def test_command_from_args():
    from snipforge.app import _command_from_args
    assert _command_from_args(["x", "--background"]) == "background"
    assert _command_from_args(["x", "--snip"]) == "snip"
    assert _command_from_args(["x"]) == "home"


# --- library window ------------------------------------------------------------------
# --- pin window ----------------------------------------------------------------------
def test_pin_window_zoom_fade_and_close(qapp, white_image):
    from snipforge.pin import PinWindow
    pin = PinWindow(white_image, QPoint(10, 10))
    pin.show()
    start = pin.width()

    def wheel(delta, mods=Qt.NoModifier):
        QApplication.sendEvent(pin, QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, delta),
                                                Qt.NoButton, mods, Qt.NoScrollPhase, False))

    wheel(120)
    assert pin.width() > start
    wheel(-120, Qt.ControlModifier)
    assert pin.windowOpacity() < 1.0
    pin._actual_size()
    pin.grab()
    mouse(pin, QEvent.MouseButtonPress, (5, 5))
    mouse(pin, QEvent.MouseMove, (15, 15))
    mouse(pin, QEvent.MouseButtonRelease, (15, 15), buttons=Qt.NoButton)
    QApplication.sendEvent(pin, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert not pin.isVisible()


# --- single instance -----------------------------------------------------------------
def test_second_launch_hands_command_to_running_instance(qapp, monkeypatch):
    """A second SnipForge process (e.g. a taskbar click) passes its command to the running one."""
    import subprocess
    import sys
    import time

    from PySide6.QtNetwork import QLocalSocket

    from snipforge import single_instance
    name = "SnipForge-pytest-instance"  # never the real app's server
    monkeypatch.setattr(single_instance, "SERVER_NAME", name)
    server = single_instance.InstanceServer()
    received = []
    server.command.connect(received.append)
    assert server.listening
    client = f"from snipforge import single_instance as s; s.SERVER_NAME = {name!r}; s.send_to_running('snip')"
    launcher = subprocess.Popen([sys.executable, "-c", client])
    deadline = time.time() + 10
    while not received and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    launcher.wait(timeout=10)
    server.close()
    assert received == ["snip"]
    # Let the client's disconnect reach the server before it goes away; a pipe torn down with I/O
    # still pending crashes whichever later test next runs the event loop.
    deadline = time.time() + 2
    while server._server.findChildren(QLocalSocket) and time.time() < deadline:
        QApplication.processEvents()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        time.sleep(0.01)
    server._server.close()


# --- packaged self-test (in-process) -------------------------------------------------
def test_selftest_report(tmp_path, monkeypatch, qapp):
    import json

    from snipforge import selftest
    monkeypatch.setattr(selftest, "_ocr", lambda: "skipped in unit tests")
    monkeypatch.setattr(selftest, "_search_index", lambda: "skipped in unit tests")
    monkeypatch.setattr(selftest, "_hotkey", lambda: "skipped in unit tests")
    report = tmp_path / "report.json"
    assert selftest.run(str(report)) == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["passed"] and data["checks"]["editor"]["ok"] and data["checks"]["icons"]["ok"]
    assert data["checks"]["theme"]["ok"] and "snipforge" in data["checks"]["theme"]["detail"]
    assert qapp.styleSheet() == ""  # the check leaves the test app as it found it


def test_selftest_reports_failures(tmp_path, monkeypatch, qapp):
    from snipforge import selftest

    def boom():
        raise RuntimeError("broken")

    monkeypatch.setattr(selftest, "_ocr", boom)
    monkeypatch.setattr(selftest, "_search_index", lambda: "skipped in unit tests")
    monkeypatch.setattr(selftest, "_hotkey", lambda: "ok")
    assert selftest.run(str(tmp_path / "r.json")) == 1
