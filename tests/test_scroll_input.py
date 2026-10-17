"""Scrolling a window for scrolling capture: real pointer moves and wheel turns (Windows)."""
import sys
import time

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea

from snipforge import scroll_input


def test_scroll_needs_at_least_one_notch():
    with pytest.raises(ValueError):
        scroll_input.scroll_down(0)


def test_available_only_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert scroll_input.available() is False
    monkeypatch.setattr(sys, "platform", "win32")
    assert scroll_input.available() is True


def test_wheel_input_is_a_downward_wheel_event(monkeypatch):
    if sys.platform != "win32":
        pytest.skip("Windows input structures")
    from snipforge import _scroll_win32
    sent = []

    def fake_send(count, event, size):
        event = event._obj  # what ctypes.byref points at
        sent.append((count, event.type, event.u.mi.dwFlags, _signed(event.u.mi.mouseData), size))
        return 1

    monkeypatch.setattr(_scroll_win32._user32, "SendInput", fake_send)
    scroll_input.scroll_down(3)
    assert sent == [(1, _scroll_win32.INPUT_MOUSE, _scroll_win32.MOUSEEVENTF_WHEEL, -360,
                     _ctypes_size(_scroll_win32.INPUT))]


def _signed(value: int) -> int:
    return value - (1 << 32) if value >= 1 << 31 else value


def _ctypes_size(kind) -> int:
    import ctypes
    return ctypes.sizeof(kind)


@pytest.mark.desktop
@pytest.mark.skipif(sys.platform != "win32", reason="real Windows input")
def test_real_wheel_turns_scroll_the_window_under_the_pointer(qapp):
    area = QScrollArea()
    page = QLabel("\n".join(f"line {n}" for n in range(400)))
    area.setWidget(page)
    area.resize(300, 300)
    area.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    area.show()
    area.raise_()
    area.activateWindow()
    QApplication.processEvents()
    time.sleep(0.3)
    QApplication.processEvents()
    saved = scroll_input.pointer_position()
    try:
        center = area.mapToGlobal(area.rect().center())
        target = QPoint(round(center.x() * area.devicePixelRatioF()), round(center.y() * area.devicePixelRatioF()))
        scroll_input.place_pointer(target)
        assert (scroll_input.pointer_position() - target).manhattanLength() <= 2
        before = area.verticalScrollBar().value()
        scroll_input.scroll_down(2)
        deadline = time.time() + 3
        while area.verticalScrollBar().value() == before and time.time() < deadline:
            QApplication.processEvents()
            time.sleep(0.02)
        assert area.verticalScrollBar().value() > before
    finally:
        scroll_input.place_pointer(saved)
        area.close()
