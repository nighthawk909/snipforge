"""The capture toolbar's delay (a countdown, then the screen freezes) and the include-pointer toggle."""
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeyEvent, QMouseEvent, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snipforge import countdown, pointer, prefs
from snipforge.settings import app_settings

KEYS = ("capture_mode", "capture_delay", "capture_pointer")


@pytest.fixture(autouse=True)
def clean_prefs(monkeypatch):
    for name in KEYS:
        app_settings().remove(prefs.KEYS[name])
    monkeypatch.setattr(countdown, "SETTLE_MS", 0)
    yield
    for name in KEYS:
        app_settings().remove(prefs.KEYS[name])


def mouse(widget, etype, pos, button=Qt.LeftButton, buttons=Qt.LeftButton):
    pt = QPointF(*pos)
    QApplication.sendEvent(widget, QMouseEvent(etype, pt, widget.mapToGlobal(pt), button, buttons, Qt.NoModifier))


def key(widget, k, text=""):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyPress, k, Qt.NoModifier, text))


def red_arrow(at: QPoint) -> pointer.PointerShot:
    image = QImage(6, 6, QImage.Format_ARGB32)
    image.fill(QColor("#ff0000"))
    return pointer.PointerShot(image, at)


def make_overlay(**kwargs):
    from snipforge.capture import _ScreenOverlay
    screen = QGuiApplication.primaryScreen()
    pix = QPixmap(screen.geometry().size())
    pix.fill(QColor("#e8eaee"))
    o = _ScreenOverlay(screen, pix, [QRect(100, 80, 300, 200)], toolbar=True, **kwargs)
    o.resize(screen.geometry().size())
    o.got = []
    o.selected.connect(o.got.append)
    o.cancelled.connect(lambda: o.got.append("cancel"))
    return o


def origin():
    return QGuiApplication.primaryScreen().geometry().topLeft()


# --- remembered choices ---------------------------------------------------------------------------

def test_delay_and_pointer_are_remembered_and_validated():
    assert prefs.capture_delay() == 0 and prefs.capture_pointer() is False
    prefs.set_capture_delay(5)
    prefs.set_capture_pointer(True)
    assert prefs.capture_delay() == 5 and prefs.capture_pointer() is True
    app_settings().setValue(prefs.KEYS["capture_delay"], "7")
    assert prefs.capture_delay() == 0
    app_settings().setValue(prefs.KEYS["capture_delay"], "10")  # INI files store text
    assert prefs.capture_delay() == 10
    with pytest.raises(ValueError):
        prefs.set_capture_delay(4)


# --- the toolbar's delay menu ------------------------------------------------------------------------

def test_delay_menu_offers_the_delays_and_shows_the_choice(qapp):
    o = make_overlay(delay=0)
    picked = []
    o.toolbar.delay_picked.connect(picked.append)
    actions = o.toolbar.delay_menu.actions()
    assert [a.text() for a in actions] == ["No delay", "3 seconds", "5 seconds", "10 seconds"]
    assert actions[0].isChecked() and o.toolbar.delay_button.text().strip() == "No delay"
    actions[2].trigger()
    assert picked == [5]
    o.toolbar.set_delay(10)  # a change made elsewhere shows without announcing it
    assert picked == [5] and o.toolbar.delay_button.text().strip() == "10 s delay"
    assert o.toolbar.delay_button.isChecked() and actions[3].isChecked()
    o.toolbar.set_delay(0)
    assert not o.toolbar.delay_button.isChecked()


def test_d_opens_the_delay_menu(qapp):
    o = make_overlay()
    o.show()
    key(o, Qt.Key_D, "d")
    QApplication.processEvents()
    assert o.toolbar.delay_menu.isVisible()
    o.toolbar.delay_menu.close()
    o.close()


# --- including the pointer ----------------------------------------------------------------------------

def test_snips_include_the_pointer_only_when_asked(qapp):
    shot = red_arrow(origin() + QPoint(150, 120))
    for include, expected in ((True, "#ff0000"), (False, "#e8eaee")):
        o = make_overlay(pointer_shot=shot, include_pointer=include)
        key(o, Qt.Key_Return)
        assert o.got[0].pixelColor(152, 122).name() == expected


def test_p_and_the_button_toggle_the_pointer(qapp):
    o = make_overlay(pointer_shot=red_arrow(origin() + QPoint(150, 120)))
    changes = []
    o.pointer_changed.connect(changes.append)
    key(o, Qt.Key_P, "p")
    QTest.mouseClick(o.toolbar.pointer_button, Qt.LeftButton)
    assert changes == [True, False] and not o.include_pointer
    key(o, Qt.Key_P, "p")
    key(o, Qt.Key_Return)
    assert o.got[0].pixelColor(152, 122).name() == "#ff0000"


def test_pointer_toggle_is_off_when_there_is_no_pointer_to_add(qapp):
    o = make_overlay(pointer_shot=None, include_pointer=True)
    assert not o.toolbar.pointer_button.isEnabled()
    tip = o.toolbar.pointer_button.toolTip()
    assert "hidden" in tip or "isn't available" in tip
    key(o, Qt.Key_P, "p")
    key(o, Qt.Key_Return)
    assert o.got[0].pixelColor(152, 122).name() == "#e8eaee"


def test_toolbar_new_buttons_are_labelled(qapp):
    bar = make_overlay(pointer_shot=red_arrow(QPoint(0, 0))).toolbar
    assert bar.delay_button.accessibleName() == "Delay" and "(D)" in bar.delay_button.toolTip()
    assert bar.pointer_button.accessibleName() == "Include the mouse pointer" and "(P)" in bar.pointer_button.toolTip()
    for button in (bar.delay_button, bar.pointer_button):
        assert button.focusPolicy() == Qt.TabFocus


# --- the capture session -------------------------------------------------------------------------------

def session_with(monkeypatch, grabbed):
    from snipforge import capture
    monkeypatch.setattr(capture.pointer, "grab", lambda: grabbed.append("pointer") or red_arrow(QPoint(0, 0)))
    original = capture._grab_screen
    monkeypatch.setattr(capture, "_grab_screen", lambda screen: grabbed.append("screen") or original(screen))
    results = []
    session = capture.CaptureSession(results.append)
    return session, results


def test_no_delay_freezes_at_once_with_the_pointer_grabbed_first(qapp, monkeypatch):
    grabbed = []
    session, _ = session_with(monkeypatch, grabbed)
    session.start()
    try:
        assert session._overlays and session.countdown is None
        assert grabbed[0] == "pointer" and "screen" in grabbed  # the pointer as it was when the screen froze
    finally:
        session._finish(None)


def test_a_remembered_delay_counts_down_first(qapp, monkeypatch):
    prefs.set_capture_delay(3)
    session, _ = session_with(monkeypatch, [])
    session.start()
    try:
        assert session.countdown is not None and session.countdown.isVisible() and not session._overlays
        session.snip_now()  # e.g. the snip key pressed again
        QTest.qWait(20)
        assert session._overlays and session.countdown is None
    finally:
        session._finish(None)


def test_cancelling_the_countdown_ends_the_snip(qapp, monkeypatch):
    prefs.set_capture_delay(5)
    session, results = session_with(monkeypatch, [])
    session.start()
    QTest.mouseClick(session.countdown.cancel_button, Qt.LeftButton)
    assert results == [None] and not session._overlays


def test_picking_a_delay_counts_down_then_freezes_again(qapp, monkeypatch):
    grabbed = []
    session, results = session_with(monkeypatch, grabbed)
    session.start()
    try:
        first = list(session._overlays)
        first[0].toolbar.delay_menu.actions()[1].trigger()  # 3 seconds
        assert prefs.capture_delay() == 3
        assert session.countdown is not None and not session._overlays and results == []
        session.snip_now()
        QTest.qWait(20)
        assert session._overlays and all(o not in first for o in session._overlays)
        assert all(o.toolbar.delay_button.text().strip() == "3 s delay" for o in session._overlays)
        assert grabbed.count("pointer") == 2  # grabbed again with the new freeze
    finally:
        session._finish(None)


def test_picking_no_delay_just_remembers_it(qapp, monkeypatch):
    prefs.set_capture_delay(5)
    session, _ = session_with(monkeypatch, [])
    session.start()
    session.snip_now()
    QTest.qWait(20)
    try:
        overlays = list(session._overlays)
        overlays[0].toolbar.delay_menu.actions()[0].trigger()
        assert prefs.capture_delay() == 0 and session._overlays == overlays and session.countdown is None
    finally:
        session._finish(None)


def test_pointer_choice_is_shared_and_remembered(qapp, monkeypatch):
    session, _ = session_with(monkeypatch, [])
    session.start()
    try:
        QTest.mouseClick(session._overlays[0].toolbar.pointer_button, Qt.LeftButton)
        assert all(o.include_pointer for o in session._overlays) and prefs.capture_pointer() is True
    finally:
        session._finish(None)
    session2, _ = session_with(monkeypatch, [])
    session2.start()
    try:
        assert all(o.include_pointer for o in session2._overlays)
    finally:
        session2._finish(None)


def test_finishing_during_a_countdown_closes_it(qapp, monkeypatch):
    prefs.set_capture_delay(10)
    session, results = session_with(monkeypatch, [])
    session.start()
    timer = session.countdown
    session._finish(None)
    assert results == [None] and not timer.isVisible() and session.countdown is None


# --- from the code review ---------------------------------------------------------------------

def test_why_the_pointer_toggle_is_off(qapp, monkeypatch):
    from snipforge import capture
    monkeypatch.setattr(capture.pointer, "available", lambda: True)
    assert "hidden" in make_overlay(pointer_shot=None).toolbar.pointer_button.toolTip()
    monkeypatch.setattr(capture.pointer, "available", lambda: False)
    assert "isn't available on this computer" in make_overlay(pointer_shot=None).toolbar.pointer_button.toolTip()


def test_the_hint_mentions_every_key(qapp):
    hint = make_overlay().hint()
    assert all(k in hint for k in ("A W F", "D delay", "P pointer", "Esc"))


def test_opening_and_closing_the_delay_menu_leaves_its_highlight_alone(qapp):
    o = make_overlay(delay=5)
    o.show()
    button = o.toolbar.delay_button
    for delay in (5, 0):
        o.toolbar.set_delay(delay)
        button.click()  # what Enter or Space on the focused button does
        QApplication.processEvents()
        o.toolbar.delay_menu.close()
        assert button.isChecked() is (delay > 0)
    o.close()


def test_d_during_a_drag_does_nothing(qapp):
    o = make_overlay()
    o.show()
    mouse(o, QEvent.MouseButtonPress, (10, 300))
    mouse(o, QEvent.MouseMove, (200, 400), Qt.NoButton)
    key(o, Qt.Key_D, "d")
    QApplication.processEvents()
    assert not o.toolbar.delay_menu.isVisible()
    mouse(o, QEvent.MouseButtonRelease, (200, 400), buttons=Qt.NoButton)
    assert o.got[0].size().toTuple() == (190, 100)  # the drag carried on normally
    o.close()


def test_a_countdown_that_cannot_start_ends_the_snip(qapp, monkeypatch):
    prefs.set_capture_delay(3)
    session, results = session_with(monkeypatch, [])

    def broken(self):
        raise RuntimeError("no screen")
    monkeypatch.setattr(countdown.Countdown, "start", broken)
    session.start()
    assert results == [None] and session.countdown is None  # not stuck: the next snip can start


def test_picking_a_delay_when_the_countdown_cannot_start_ends_the_snip(qapp, monkeypatch):
    session, results = session_with(monkeypatch, [])
    session.start()

    def broken(*_args, **_kwargs):
        raise RuntimeError("no screen")
    from snipforge import capture
    monkeypatch.setattr(capture, "Countdown", broken)
    session._overlays[0].toolbar.delay_menu.actions()[1].trigger()
    assert results == [None] and not session._overlays


def test_finishing_during_the_settle_wait_never_fires_late(qapp, monkeypatch):
    monkeypatch.setattr(countdown, "SETTLE_MS", 40)
    prefs.set_capture_delay(3)
    session, results = session_with(monkeypatch, [])
    session.start()
    session.snip_now()  # hides, then waits 40 ms before reporting
    session.cancel()
    QTest.qWait(80)
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QTest.qWait(20)
    assert results == [None] and not session._overlays  # no late freeze after the cancel


def test_session_cancel_works_at_any_stage(qapp, monkeypatch):
    session, results = session_with(monkeypatch, [])
    session.start()
    session.cancel()
    session.cancel()
    assert results == [None] and not session._overlays


def test_composite_with_an_offset_monitor_and_scaling(qapp):
    screen = QImage(40, 40, QImage.Format_RGB32)
    screen.fill(QColor("#ffffff"))
    screen.setDevicePixelRatio(1.5)
    shot = red_arrow(QPoint(110, 60))  # this monitor starts at (100, 50)
    result = pointer.composite(screen, shot, origin=QPoint(100, 50))
    assert result.pixelColor(10, 10).name() == "#ff0000" and result.pixelColor(9, 10).name() == "#ffffff"


def test_the_pointer_image_is_worked_out_only_when_needed(qapp, monkeypatch):
    calls = []
    real = pointer.with_alpha

    def counting(*args):
        calls.append(1)
        return real(*args)
    monkeypatch.setattr(pointer, "with_alpha", counting)
    black, white = QImage(4, 4, QImage.Format_RGB32), QImage(4, 4, QImage.Format_RGB32)
    black.fill(QColor("#000000"))
    white.fill(QColor("#ffffff"))
    shot = pointer.PointerShot(position=QPoint(0, 0), drawings=(black, white))
    o = make_overlay(pointer_shot=shot, include_pointer=False)
    key(o, Qt.Key_Return)  # a snip without the pointer never pays for it
    assert calls == []
    assert shot.image.width() == 4 and shot.inverted == () and len(calls) == 1
    assert shot.image.width() == 4 and len(calls) == 1  # worked out once


def test_pointer_trouble_never_stops_a_snip(monkeypatch):
    import sys

    def broken():
        raise RuntimeError("unexpected")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(pointer, "_grab_windows", broken)
    assert pointer.grab() is None
