"""Desktop test for Pick a color: a real window on a real monitor, frozen with Windows' own screen
grab at that monitor's real display scale, picked to the exact screen pixel, and copied to the real
clipboard.

    QT_QPA_PLATFORM=windows python -m pytest -m desktop tests/desktop/test_color_picker_desktop.py

The real pointer isn't moved and no keys are pressed: the picker's overlay gets Qt's events at the
positions Windows reports them (whole screen pixels divided by the display scale).
"""
import ctypes
import math
import time
from ctypes import wintypes

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QKeyEvent, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from snipforge import prefs, shortcuts
from snipforge.editor import colors
from snipforge.settings import app_settings
from tests.test_capture_fixed import coded_screen

pytestmark = pytest.mark.desktop


@pytest.fixture(scope="module", autouse=True)
def real_platform(qapp):
    if QGuiApplication.platformName() == "offscreen":
        pytest.skip("desktop tests need a real display: set QT_QPA_PLATFORM=windows")


@pytest.fixture(autouse=True)
def clean_prefs():
    keys = [prefs.KEYS["color_format"], colors.RECENT_SETTING]
    for k in keys:
        app_settings().remove(k)
    clipboard = QApplication.clipboard().text()
    yield
    for k in keys:
        app_settings().remove(k)
    if clipboard:
        QApplication.clipboard().setText(clipboard)


def pump(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)


class Coded(QWidget):
    """Every screen pixel of it says where it is in the window (tests/test_capture_fixed.py)."""

    def __init__(self) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.resize(240, 160)

    def paintEvent(self, _e) -> None:  # noqa: N802 - Qt API
        ratio = self.devicePixelRatioF()
        image = coded_screen(math.ceil(self.width() * ratio), math.ceil(self.height() * ratio))
        image.setDevicePixelRatio(ratio)
        p = QPainter(self)
        p.drawImage(0, 0, image)
        p.end()


def window_pixel(color: QColor) -> tuple[int, int]:
    red, green, blue = color.red(), color.green(), color.blue()
    return (blue >> 4) << 8 | red, (blue & 15) << 8 | green


def client_origin(window: QWidget) -> tuple[int, int]:
    """Where Windows has the window's top-left pixel, in physical screen pixels."""
    point = wintypes.POINT(0, 0)
    assert ctypes.windll.user32.ClientToScreen(wintypes.HWND(int(window.winId())), ctypes.byref(point))
    return point.x, point.y


def send(widget, etype, pos, button=Qt.NoButton, buttons=Qt.NoButton):
    pt = QPointF(*pos)
    QApplication.sendEvent(widget, QMouseEvent(etype, pt, widget.mapToGlobal(pt), button, buttons, Qt.NoModifier))


@pytest.fixture
def coded_window(qapp):
    window = Coded()
    screen = QGuiApplication.primaryScreen().availableGeometry()
    window.move(screen.center() - window.rect().center())
    window.show()
    window.raise_()
    pump(0.6)  # shown and painted before the screen freezes
    yield window
    window.close()


def start_picking(window):
    from snipforge.color_picker import START_DELAY_MS, ColorPicker
    notes = []
    picker = ColorPicker(notes.append)
    picker.start()
    pump(START_DELAY_MS / 1000 + 0.1)  # it waits a moment before freezing, as a snip does
    assert picker.session is not None and picker.session.overlays, notes
    overlay = next(o for o in picker.session.overlays if o.capture_screen is window.screen())
    pump(0.3)
    return picker, overlay, notes


def monitor_pixel(window, overlay, x: int, y: int) -> tuple[int, int]:
    """The window's pixel (x, y), in its monitor's screen pixels (Qt keeps each monitor's top-left
    where Windows has it)."""
    left, top = client_origin(window)
    origin = overlay.capture_screen.geometry().topLeft()
    return left - origin.x() + x, top - origin.y() + y


def test_a_click_picks_the_exact_screen_pixel_of_a_real_window(coded_window):
    picker, overlay, notes = start_picking(coded_window)
    try:
        assert overlay._dpr == pytest.approx(coded_window.screen().devicePixelRatio())
        mx, my = monitor_pixel(coded_window, overlay, 37, 23)
        at = (mx / overlay._dpr, my / overlay._dpr)  # as Windows reports the pointer there
        send(overlay, QEvent.MouseMove, at)
        send(overlay, QEvent.MouseButtonPress, at, Qt.LeftButton, Qt.LeftButton)
        send(overlay, QEvent.MouseButtonRelease, at, Qt.LeftButton)
        pump(0.2)
        expected = QColor(37, 23, 0).name().upper()  # the coded window's pixel (37, 23)
        assert QApplication.clipboard().text() == expected
        assert colors.recent()[0] == expected.lower()
        assert notes == [f"Copied {expected}\nRGB 37, 23, 0"]
        assert picker.session is None
    finally:
        picker.cancel()


def test_arrow_keys_move_exactly_one_screen_pixel_on_a_real_monitor(coded_window):
    picker, overlay, _notes = start_picking(coded_window)
    try:
        mx, my = monitor_pixel(coded_window, overlay, 100, 60)
        send(overlay, QEvent.MouseMove, (mx / overlay._dpr, my / overlay._dpr))
        assert window_pixel(overlay.color()) == (100, 60)
        for k, expected in ((Qt.Key_Right, (101, 60)), (Qt.Key_Down, (101, 61)), (Qt.Key_Left, (100, 61))):
            QApplication.sendEvent(overlay, QKeyEvent(QEvent.KeyPress, k, Qt.NoModifier))
            assert window_pixel(overlay.color()) == expected
        QApplication.sendEvent(overlay, QKeyEvent(QEvent.KeyPress, Qt.Key_Tab, Qt.NoModifier))
        QApplication.sendEvent(overlay, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
        pump(0.2)
        assert QApplication.clipboard().text() == "rgb(100, 61, 0)"
        assert prefs.color_format() == "rgb"
    finally:
        picker.cancel()


def test_the_pointer_position_is_read_in_windows_physical_pixels(coded_window):
    """The keyboard starts from the pointer's own pixel: Windows' position, which at any display
    scale matches Qt's (scaled, rounded) one to within a pixel's scaling."""
    from PySide6.QtGui import QCursor

    from snipforge.color_picker import _pointer
    physical, logical = _pointer(), QCursor.pos()
    screen = QGuiApplication.screenAt(logical) or QGuiApplication.primaryScreen()
    origin, ratio = screen.geometry().topLeft(), screen.devicePixelRatio()
    expected = origin + (logical - origin) * ratio
    assert abs(physical.x() - expected.x()) <= ratio and abs(physical.y() - expected.y()) <= ratio
    assert isinstance(physical, QPoint)


def test_a_pick_a_color_shortcut_reaches_snipforge_through_windows(qapp):
    keys = shortcuts.GlobalShortcuts()
    fired = []
    keys.triggered.connect(fired.append)
    try:
        assert keys.set("color", "Ctrl+Alt+Shift+F22") is None  # nobody's: no keyboard has an F22 key
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        thread = ctypes.WinDLL("kernel32").GetCurrentThreadId()
        assert user32.PostThreadMessageW(thread, shortcuts.WM_HOTKEY, keys._id("color"), 0)
        pump(0.3)
        assert fired == ["color"]
    finally:
        keys.close()
