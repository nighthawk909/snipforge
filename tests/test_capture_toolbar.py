"""The capture toolbar and its modes: Area (drag an area or click a window), Window and Full screen."""
import pytest
from PySide6.QtCore import QEvent, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QKeyEvent, QMouseEvent, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snipforge import prefs
from snipforge.settings import app_settings

WINDOW = QRect(100, 80, 300, 200)


@pytest.fixture(autouse=True)
def clean_prefs():
    app_settings().remove(prefs.KEYS["capture_mode"])
    yield
    app_settings().remove(prefs.KEYS["capture_mode"])


def mouse(widget, etype, pos, button=Qt.LeftButton, buttons=Qt.LeftButton):
    pt = QPointF(*pos)
    QApplication.sendEvent(widget, QMouseEvent(etype, pt, widget.mapToGlobal(pt), button, buttons, Qt.NoModifier))


def click(widget, pos):
    mouse(widget, QEvent.MouseMove, pos, Qt.NoButton, Qt.NoButton)
    mouse(widget, QEvent.MouseButtonPress, pos)
    mouse(widget, QEvent.MouseButtonRelease, pos, buttons=Qt.NoButton)


def drag(widget, start, end):
    mouse(widget, QEvent.MouseButtonPress, start)
    mouse(widget, QEvent.MouseMove, end, Qt.NoButton)
    mouse(widget, QEvent.MouseButtonRelease, end, buttons=Qt.NoButton)


def key(widget, k, text=""):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyPress, k, Qt.NoModifier, text))


def make_overlay(mode="area", toolbar=False):
    from snipforge.capture import _ScreenOverlay
    screen = QGuiApplication.primaryScreen()
    pix = QPixmap(screen.geometry().size())
    pix.fill(QColor("#e8eaee"))
    o = _ScreenOverlay(screen, pix, [WINDOW], mode=mode, toolbar=toolbar)
    o.resize(screen.geometry().size())
    o.got = []
    o.selected.connect(o.got.append)
    o.cancelled.connect(lambda: o.got.append("cancel"))
    return o


# --- the remembered mode ---------------------------------------------------------------------

def test_the_toolbar_and_prefs_know_the_same_modes():
    from snipforge.capture_toolbar import MODES
    assert tuple(MODES) == prefs.CAPTURE_MODES


def test_capture_mode_is_remembered_and_validated():
    assert prefs.capture_mode() == "area"
    prefs.set_capture_mode("window")
    assert prefs.capture_mode() == "window"
    app_settings().setValue(prefs.KEYS["capture_mode"], "scrolling-into-the-void")
    assert prefs.capture_mode() == "area"
    with pytest.raises(ValueError):
        prefs.set_capture_mode("everything")


# --- what each mode does -----------------------------------------------------------------------

def test_area_mode_drags_an_area_or_takes_the_window_under_a_click(qapp):
    o = make_overlay("area")
    drag(o, (10, 10), (110, 70))
    click(o, (200, 150))
    assert [i.size().toTuple() for i in o.got] == [(100, 60), (300, 200)]


def test_window_mode_takes_only_windows(qapp):
    o = make_overlay("window")
    drag(o, (110, 90), (300, 250))  # a drag inside a window still takes the whole window
    assert o.got[0].size().toTuple() == (300, 200)
    w, h = o.width(), o.height()
    click(o, (w - 5, h - 5))  # the desktop isn't a window here
    assert len(o.got) == 1


def test_full_screen_mode_takes_the_monitor_wherever_you_click(qapp):
    o = make_overlay("screen")
    click(o, (200, 150))  # even on top of a window
    drag(o, (10, 10), (110, 70))  # and dragging doesn't choose an area
    assert [i.size() for i in o.got] == [o.size(), o.size()]


@pytest.mark.parametrize("mode", ["area", "window", "screen"])
def test_enter_and_esc_work_in_every_mode(qapp, mode):
    o = make_overlay(mode)
    key(o, Qt.Key_Return)
    key(o, Qt.Key_Escape)
    assert o.got[0].size() == o.size() and o.got[1] == "cancel"


def test_letter_keys_switch_modes(qapp):
    o = make_overlay("area")
    switched = []
    o.mode_changed.connect(switched.append)
    key(o, Qt.Key_W, "w")
    key(o, Qt.Key_F, "f")
    key(o, Qt.Key_A, "a")
    assert switched == ["window", "screen", "area"] and o.mode == "area"


def test_each_mode_explains_itself(qapp):
    from snipforge.capture_toolbar import MODES
    o = make_overlay("area")
    hints = set()
    for mode in MODES:
        o.set_mode(mode)
        hints.add(o.hint())
        assert "Esc" in o.hint()
    assert len(hints) == len(MODES)


# --- the toolbar -------------------------------------------------------------------------------

def test_overlay_without_toolbar_has_none(qapp):
    assert make_overlay().toolbar is None


def test_toolbar_buttons_switch_modes(qapp):
    o = make_overlay("area", toolbar=True)
    switched = []
    o.mode_changed.connect(switched.append)
    QTest.mouseClick(o.toolbar.buttons["window"], Qt.LeftButton)
    assert o.mode == "window" and switched == ["window"]
    assert o.toolbar.buttons["window"].isChecked() and not o.toolbar.buttons["area"].isChecked()
    o.set_mode("screen")  # a change made elsewhere (another monitor) shows on the toolbar
    assert o.toolbar.buttons["screen"].isChecked()


def test_toolbar_cancel_button_cancels(qapp):
    o = make_overlay(toolbar=True)
    QTest.mouseClick(o.toolbar.cancel_button, Qt.LeftButton)
    assert o.got == ["cancel"]


def test_toolbar_is_labelled_for_screen_readers_and_shows_shortcuts(qapp):
    from snipforge.capture_toolbar import MODES
    o = make_overlay(toolbar=True)
    assert o.toolbar.accessibleName() == "Capture"
    for mode, spec in MODES.items():
        button = o.toolbar.buttons[mode]
        assert button.text().strip() == spec.label and button.accessibleName() == spec.label
        assert f"({spec.key})" in button.toolTip()
        assert button.focusPolicy() == Qt.TabFocus  # a click mustn't take the keyboard from the overlay
    assert o.toolbar.cancel_button.accessibleName() == "Cancel"


def test_toolbar_sits_at_the_top_and_steps_aside_while_dragging(qapp):
    o = make_overlay(toolbar=True)
    o.show()
    QApplication.processEvents()
    bar = o.toolbar
    assert bar.isVisible() and bar.y() < 40
    assert abs(bar.geometry().center().x() - o.rect().center().x()) <= 1
    mouse(o, QEvent.MouseButtonPress, (10, 300))
    mouse(o, QEvent.MouseMove, (60, 340), Qt.NoButton)
    assert not bar.isVisible()
    mouse(o, QEvent.MouseButtonRelease, (60, 340), buttons=Qt.NoButton)
    assert bar.isVisible()
    o.close()


def test_snips_come_from_the_frozen_screen_not_the_toolbar(qapp):
    o = make_overlay(toolbar=True)
    o.show()
    QApplication.processEvents()
    top = o.toolbar.geometry()
    drag(o, (top.left() - 20, 0), (top.right() + 20, top.bottom() + 20))
    image = o.got[0]
    assert {image.pixelColor(x, y).name() for x in range(0, image.width(), 7)
            for y in range(0, image.height(), 7)} == {"#e8eaee"}
    o.close()


# --- the capture session --------------------------------------------------------------------------

def test_session_puts_one_toolbar_on_the_active_monitor_and_shares_the_mode(qapp):
    from snipforge.capture import CaptureSession
    prefs.set_capture_mode("window")
    session = CaptureSession(lambda image: None)
    session.start()
    try:
        with_bar = [o for o in session._overlays if o.toolbar is not None]
        assert len(with_bar) == 1
        assert all(o.mode == "window" for o in session._overlays)  # starts in the remembered mode
        QTest.mouseClick(with_bar[0].toolbar.buttons["screen"], Qt.LeftButton)
        assert all(o.mode == "screen" for o in session._overlays)
        assert prefs.capture_mode() == "screen"  # and the next snip starts there
    finally:
        session._finish(None)


def test_session_still_takes_the_keyboard_when_the_pointer_is_off_every_screen(qapp, monkeypatch):
    from snipforge.capture import CaptureSession
    monkeypatch.setattr(QGuiApplication, "screenAt", staticmethod(lambda _pos: None))
    session = CaptureSession(lambda image: None)
    session.start()
    try:
        primary = QGuiApplication.primaryScreen()
        active = [o for o in session._overlays if o.toolbar is not None]
        assert len(active) == 1 and active[0].screen() is primary
    finally:
        session._finish(None)

