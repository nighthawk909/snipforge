"""The capture toolbar and its modes: Area (drag an area or click a window), Window and Full screen."""
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QKeyEvent, QMouseEvent, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame

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
    from snipforge import scroll_input
    # Scrolling is offered only where SnipForge can scroll other windows (Windows, for now).
    assert ("scroll" in o.toolbar.buttons) == scroll_input.available()
    for mode, button in o.toolbar.buttons.items():
        spec = MODES[mode]
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
    # Centred as it is in Fixed size mode (with its size button), so switching never moves a button.
    assert abs(bar.x() + bar.widest() / 2 - o.width() / 2) <= 1
    mouse(o, QEvent.MouseButtonPress, (10, 300))
    mouse(o, QEvent.MouseMove, (60, 340), Qt.NoButton)
    assert not bar.isVisible()
    mouse(o, QEvent.MouseButtonRelease, (60, 340), buttons=Qt.NoButton)
    assert bar.isVisible()
    o.close()


def shown(mode="area"):
    """An overlay with its toolbar, shown, so clicks go through the real window like a mouse's."""
    o = make_overlay(mode, toolbar=True)
    o.show()
    QApplication.processEvents()
    return o


def window_click(o, pos: QPoint, button=Qt.LeftButton):
    QTest.mouseClick(o.windowHandle(), button, Qt.NoModifier, pos)
    QApplication.processEvents()


@pytest.mark.parametrize("mode", ["area", "window", "screen"])
def test_clicks_on_the_toolbar_never_snip_what_is_behind_it(qapp, mode):
    o = shown(mode)
    bar = o.toolbar.geometry()
    for pos in (QPoint(bar.left() + 2, bar.center().y()),  # its padding
                QPoint(bar.right() - 1, bar.top() + 1),  # its border
                o.toolbar.findChild(QFrame, "CaptureToolbarSeparator").geometry().center() + bar.topLeft()):
        window_click(o, pos)
    assert o.got == []
    o.close()


def test_right_click_on_the_toolbar_cancels_like_anywhere_else(qapp):
    o = shown()
    button = o.toolbar.buttons["window"]
    window_click(o, button.geometry().center() + o.toolbar.pos(), Qt.RightButton)
    assert o.got == ["cancel"] and o.mode == "area"
    o.close()


def test_switching_modes_mid_drag_drops_the_drag(qapp):
    o = shown("area")
    mouse(o, QEvent.MouseButtonPress, (10, 300))
    mouse(o, QEvent.MouseMove, (200, 400), Qt.NoButton)
    key(o, Qt.Key_W, "w")
    assert o.toolbar.isVisible()
    mouse(o, QEvent.MouseButtonRelease, (200, 400), buttons=Qt.NoButton)
    assert o.got == [] and o.mode == "window"
    o.close()


def test_enter_presses_a_focused_toolbar_button_instead_of_snipping(qapp):
    o = shown()
    for button in (o.toolbar.buttons["window"], o.toolbar.cancel_button):
        QApplication.sendEvent(button, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    assert o.mode == "window" and o.got == ["cancel"]  # never a whole-monitor snip
    o.close()


def test_clicking_a_toolbar_button_leaves_the_keyboard_with_the_overlay(qapp):
    o = shown()
    o.activateWindow()
    o.setFocus()
    QApplication.processEvents()
    button = o.toolbar.buttons["screen"]
    window_click(o, button.geometry().center() + o.toolbar.pos())
    assert o.mode == "screen" and not button.hasFocus()
    o.close()


def test_mode_letters_ignore_ctrl_and_alt_but_allow_shift(qapp):
    o = make_overlay("area")
    for mods in (Qt.ControlModifier, Qt.AltModifier, Qt.ControlModifier | Qt.ShiftModifier):
        QApplication.sendEvent(o, QKeyEvent(QEvent.KeyPress, Qt.Key_W, mods, "w"))
    assert o.mode == "area"
    QApplication.sendEvent(o, QKeyEvent(QEvent.KeyPress, Qt.Key_W, Qt.ShiftModifier, "W"))
    assert o.mode == "window"


def test_pointer_on_the_toolbar_hides_the_magnifier_and_highlight(qapp):
    o = shown()
    mouse(o, QEvent.MouseMove, (200, 150), Qt.NoButton, Qt.NoButton)
    assert o._cursor is not None and o._hover is not None
    QApplication.sendEvent(o.toolbar, QEvent(QEvent.Enter))
    assert o._cursor is None and o._hover is None
    o.close()


def test_toolbar_buttons_show_keyboard_focus_and_hints_mention_the_letters(qapp):
    from snipforge.theme import styles
    assert "#CaptureToolbar QToolButton:focus" in Path(styles.__file__).read_text(encoding="utf-8")
    assert "A W F" in make_overlay().hint()


def test_snips_come_from_the_frozen_screen_not_the_toolbar(qapp):
    from snipforge.capture import _ScreenOverlay
    screen = QGuiApplication.primaryScreen()
    pix = QPixmap(screen.geometry().size())
    pix.fill(QColor("#e8eaee"))
    painter = QPainter(pix)
    painter.fillRect(0, 0, pix.width(), 80, QColor("#ff0000"))  # the frozen screen is red behind the toolbar
    painter.end()
    o = _ScreenOverlay(screen, pix, [WINDOW], toolbar=True)
    o.resize(screen.geometry().size())
    o.got = []
    o.selected.connect(o.got.append)
    o.show()
    QApplication.processEvents()
    bar = o.toolbar.geometry()
    drag(o, (bar.left() - 20, 0), (bar.right() + 20, bar.bottom() + 4))
    image = o.got[0]
    assert {image.pixelColor(x, y).name() for x in range(0, image.width(), 5)
            for y in range(0, image.height(), 5)} == {"#ff0000"}
    o.close()


# --- the capture session --------------------------------------------------------------------------

def test_every_monitor_gets_the_toolbar_and_they_stay_in_step(qapp):
    from snipforge.capture import CaptureSession
    prefs.set_capture_mode("window")
    session = CaptureSession(lambda image: None)
    session.start()
    try:
        assert session._overlays and all(o.toolbar is not None for o in session._overlays)
        assert all(o.mode == "window" for o in session._overlays)  # starts in the remembered mode
        QTest.mouseClick(session._overlays[0].toolbar.buttons["screen"], Qt.LeftButton)
        assert all(o.mode == "screen" and o.toolbar.buttons["screen"].isChecked() for o in session._overlays)
        assert prefs.capture_mode() == "screen"  # and the next snip starts there
    finally:
        session._finish(None)


def test_main_monitor_takes_the_keyboard_when_the_pointer_is_off_every_screen(qapp, monkeypatch):
    from snipforge.capture import CaptureSession
    monkeypatch.setattr(QGuiApplication, "screenAt", staticmethod(lambda _pos: None))
    session = CaptureSession(lambda image: None)
    session.start()
    try:
        assert session.focus is not None and session.focus.screen() is QGuiApplication.primaryScreen()
    finally:
        session._finish(None)


def test_toolbar_fits_a_narrow_monitor_with_icons_only(qapp):
    o = shown()
    for width in (1920, 700):
        o.resize(width, 600)
        QApplication.processEvents()
        bar = o.toolbar.geometry()
        assert bar.left() >= 0 and bar.right() < width  # never hangs off the edge
    button = o.toolbar.buttons["window"]
    assert button.toolButtonStyle() == Qt.ToolButtonIconOnly and button.accessibleName() == "Window"
    o.resize(3000, 600)
    QApplication.processEvents()
    assert o.toolbar.buttons["window"].toolButtonStyle() == Qt.ToolButtonTextBesideIcon
    o.close()
