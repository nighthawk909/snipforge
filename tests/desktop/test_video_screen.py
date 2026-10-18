"""Desktop tests for recording's screen and pointer: real DXGI Desktop Duplication, real windows.

Run with the rest of the desktop suite:

    QT_QPA_PLATFORM=windows python -m pytest -m desktop

They open small coloured windows, copy their area with DXGI and check the pixels. Two tests move
the mouse pointer onto their own window (one presses the left button there, right after checking
that window is what's under the pointer) and put it back afterwards. Where Windows can't duplicate the
screen (Remote Desktop, a session without a display, a basic display driver), they skip.
"""
import ctypes
import time
from ctypes import wintypes

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from snipforge.video import cursor, dxgi

pytestmark = [pytest.mark.desktop, pytest.mark.windows]

WDA_EXCLUDEFROMCAPTURE = 0x11
INPUT_MOUSE = 0
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]


@pytest.fixture(scope="module", autouse=True)
def real_platform(qapp):
    if QGuiApplication.platformName() == "offscreen":
        pytest.skip("desktop tests need a real display: set QT_QPA_PLATFORM=windows")


def pump(seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        QApplication.processEvents()
        time.sleep(0.01)


@pytest.fixture
def coloured_window(qapp):
    """Opens frameless, always-on-top windows filled with one colour each."""
    opened = []

    def open_window(colour: str, x: int = 80, y: int = 80, width: int = 320, height: int = 220) -> QWidget:
        window = QWidget(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        palette = window.palette()
        palette.setColor(QPalette.Window, QColor(colour))
        window.setPalette(palette)
        window.setAutoFillBackground(True)
        top_left = QGuiApplication.primaryScreen().availableGeometry().topLeft()
        window.setGeometry(top_left.x() + x, top_left.y() + y, width, height)
        window.show()
        window.raise_()
        opened.append(window)
        pump(0.3)
        return window

    yield open_window
    for window in opened:
        window.close()


def physical_rect(window: QWidget, inset: int = 0) -> tuple[int, int, int, int]:
    """The window's inside in physical desktop pixels (Qt makes this process per-monitor DPI aware)."""
    hwnd = int(window.winId())
    rect, origin = wintypes.RECT(), wintypes.POINT(0, 0)
    assert user32.GetClientRect(hwnd, ctypes.byref(rect)) and user32.ClientToScreen(hwnd, ctypes.byref(origin))
    return origin.x + inset, origin.y + inset, rect.right - 2 * inset, rect.bottom - 2 * inset


def open_or_skip(area) -> dxgi.AreaDuplicator:
    try:
        return dxgi.AreaDuplicator(*area)
    except dxgi.DuplicationUnavailable as exc:
        pytest.skip(f"Windows can't duplicate the screen here: {exc}")


def colours(grabbed, width: int, height: int, step: int = 7) -> set[tuple[int, int, int]]:
    """The (r, g, b) colours of a grid of pixels across the frame."""
    data, stride = grabbed
    found = set()
    for y in range(0, height, step):
        for x in range(0, width, step):
            b, g, r = data[y * stride + x * 4: y * stride + x * 4 + 3]
            found.add((r, g, b))
    return found


def close_to(found: set, colour: str, tolerance: int = 6) -> bool:
    want = QColor(colour)
    return bool(found) and all(abs(r - want.red()) <= tolerance and abs(g - want.green()) <= tolerance
                               and abs(b - want.blue()) <= tolerance for r, g, b in found)


def grab_until(dup: dxgi.AreaDuplicator, colour: str, seconds: float = 3.0):
    """Grabs frames until the whole area shows `colour`; the last frame grabbed. The test windows are
    repainted meanwhile: Windows only hands over an image when something on the monitor changes."""
    end, last = time.perf_counter() + seconds, None
    while time.perf_counter() < end:
        for window in QApplication.topLevelWidgets():
            window.update()
        QApplication.processEvents()
        grabbed = dup.grab(50)
        if grabbed is not None:
            last = grabbed
            if close_to(colours(grabbed, dup.width, dup.height), colour):
                break
    assert last is not None, f"no frame from the duplicator in {seconds} s"
    return last


def test_a_windows_area_is_copied_with_its_pixels(coloured_window):
    window = coloured_window("#ff00ff")
    area = physical_rect(window, inset=20)
    with open_or_skip(area) as dup:
        grabbed = grab_until(dup, "#ff00ff")
        data, stride = grabbed
        assert stride >= area[2] * 4 and len(data) >= stride * area[3]
        assert isinstance(data, bytearray)  # the recorder's own copy, to draw the pointer into
        assert close_to(colours(grabbed, *area[2:]), "#ff00ff"), colours(grabbed, *area[2:])

        # The window changes colour: the next frames follow it.
        palette = window.palette()
        palette.setColor(QPalette.Window, QColor("#00b4b4"))
        window.setPalette(palette)
        window.update()
        assert close_to(colours(grab_until(dup, "#00b4b4"), *area[2:]), "#00b4b4")
        assert abs(dup.frame_time - time.perf_counter()) < 2.0  # stamped on time.perf_counter()'s clock
        assert dup.reopens == 0 and dup.protected_content is False


def test_a_window_hidden_from_capture_is_left_out(coloured_window):
    coloured_window("#00c000", x=60, y=60, width=400, height=300)
    hidden = coloured_window("#ff0000", x=140, y=120, width=200, height=150)
    area = physical_rect(hidden, inset=10)
    with open_or_skip(area) as dup:
        assert close_to(colours(grab_until(dup, "#ff0000"), *area[2:]), "#ff0000"), "the red window isn't on screen"
        if not user32.SetWindowDisplayAffinity(int(hidden.winId()), WDA_EXCLUDEFROMCAPTURE):
            pytest.skip("this Windows can't exclude a window from capture (needs Windows 10 2004)")
        hidden.update()
        found = colours(grab_until(dup, "#00c000"), *area[2:])
        assert close_to(found, "#00c000"), found  # what's behind it shows instead


def test_areas_off_one_monitor_are_refused():
    monitors = dxgi.monitors()
    if not monitors:
        pytest.skip("no monitor attached to the desktop")
    first = monitors[0]
    with pytest.raises(dxgi.AreaError):
        dxgi.AreaDuplicator(first.right - 50, first.top, 100, 100)
    with pytest.raises(dxgi.AreaError, match="isn't on any monitor"):
        dxgi.AreaDuplicator(-10**6, -10**6, 100, 100)


def test_restarting_the_duplication_keeps_copying(coloured_window):
    # What grab() does after Windows takes the duplication away (UAC prompt, lock screen), which a
    # test can't trigger: a new duplication on the same graphics device, or on a new device.
    window = coloured_window("#c08000")
    area = physical_rect(window, inset=10)
    with open_or_skip(area) as dup:
        grab_until(dup, "#c08000")
        for new_device in (False, True, False):
            started = time.perf_counter()
            dup._session.restart(new_device=new_device)
            took = time.perf_counter() - started
            assert close_to(colours(grab_until(dup, "#c08000"), *area[2:]), "#c08000")
            print(f"restart (new device: {new_device}): {took * 1000:.0f} ms")


def test_opening_and_closing_again_and_again_leaves_nothing_behind(coloured_window):
    area = physical_rect(coloured_window("#008080"), inset=10)
    for _ in range(8):
        with open_or_skip(area) as dup:
            grab_until(dup, "#008080")


def test_an_unaware_thread_still_works_in_physical_pixels(coloured_window):
    # Qt makes the process per-monitor DPI aware, but a thread (or a process started some other
    # way) may not be: DuplicateOutput1 would be refused and pointer positions scaled.
    area = physical_rect(coloured_window("#8000c0"), inset=10)
    where = cursor.system_source().state()
    set_context = user32.SetThreadDpiAwarenessContext
    set_context.argtypes, set_context.restype = [ctypes.c_void_p], ctypes.c_void_p
    previous = set_context(ctypes.c_void_p(-1))  # DPI_AWARENESS_CONTEXT_UNAWARE
    try:
        with open_or_skip(area) as dup:
            data = grab_until(dup, "#8000c0")
        unaware_where = cursor.system_source().state()
    finally:
        set_context(ctypes.c_void_p(previous))
    assert close_to(colours(data, *area[2:]), "#8000c0")
    if where is not None and unaware_where is not None:
        assert abs(unaware_where.x - where.x) <= 2 and abs(unaware_where.y - where.y) <= 2


def under_pointer(window: QWidget) -> bool:
    """Whether the window is what a click would land on right now."""
    now = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(now))
    return user32.GetAncestor(user32.WindowFromPoint(now), 2) == int(window.winId())  # GA_ROOT


@pytest.fixture
def pointer_on(coloured_window):
    """Moves the pointer to the middle of a new window (and back afterwards): (physical rect, window)."""
    window = coloured_window("#3366cc")
    area = physical_rect(window)
    saved = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(saved))
    user32.SetCursorPos(area[0] + area[2] // 2, area[1] + area[3] // 2)
    pump(0.2)
    try:
        if not under_pointer(window):
            pytest.skip("something else is on top of the test window")
        yield area, window
    finally:
        user32.SetCursorPos(saved.x, saved.y)


def test_the_pointer_is_drawn_into_a_frame_where_it_is(pointer_on):
    area, _window = pointer_on
    effects = cursor.PointerEffects(highlight=False, clicks=False)
    overlay = cursor.PointerOverlay(area, effects)
    state = cursor.system_source().state()
    if state is None or not state.visible:
        pytest.skip("the pointer is hidden (touch input, or hidden while typing)")
    with open_or_skip(area) as dup:
        data, stride = grab_until(dup, "#3366cc")
        image = QImage(data, area[2], area[3], stride, QImage.Format_RGB32).copy()
    assert close_to(colours((data, stride), *area[2:]), "#3366cc")  # Windows leaves the pointer out
    overlay.draw(image, time.perf_counter())
    middle = (area[2] // 2, area[3] // 2)
    near = {image.pixelColor(middle[0] + dx, middle[1] + dy).name() for dx in range(0, 8) for dy in range(0, 12)}
    assert near - {"#3366cc"}, "no pointer drawn next to its hotspot"
    assert image.pixelColor(5, 5).name() == "#3366cc"


def test_a_real_click_becomes_a_ring(pointer_on):
    area, window = pointer_on
    overlay = cursor.PointerOverlay(area, cursor.PointerEffects(pointer=False, highlight=False))
    frame = QImage(area[2], area[3], QImage.Format_RGB32)
    frame.fill(QColor("#3366cc"))
    overlay.draw(frame.copy(), time.perf_counter())  # the first poll only notes how the buttons are

    def press(flags: int) -> None:
        sent = INPUT(INPUT_MOUSE, MOUSEINPUT(0, 0, 0, flags, 0, 0))
        assert user32.SendInput(1, ctypes.byref(sent), ctypes.sizeof(INPUT)) == 1

    if not under_pointer(window):  # checked again just before: the click lands wherever the pointer is
        pytest.skip("the pointer left the test window")
    press(MOUSEEVENTF_LEFTDOWN)
    try:
        pump(0.05)
        clicked = time.perf_counter()
        overlay.draw(frame.copy(), clicked)
    finally:
        press(MOUSEEVENTF_LEFTUP)  # never leave the button down
    pump(0.05)
    assert overlay.clicks_seen == 1

    later = frame.copy()
    overlay.draw(later, clicked + cursor.RING_SECONDS / 2)
    assert later != frame  # the ring is drawn


def test_the_standard_pointers_render():
    source = cursor.system_source()
    shapes = [source.shape(handle) for handle in source.standard_handles()]
    drawn = [shape for shape in shapes if shape is not None]
    assert len(drawn) >= len(shapes) - 1, "most standard pointers should render"
    arrow = drawn[0]
    assert not arrow.image.isNull() and arrow.image.width() >= 16
    assert any(arrow.image.pixelColor(x, y).alpha() for x in range(arrow.image.width())
               for y in range(arrow.image.height()))
