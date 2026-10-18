"""Desktop: Windows' sleep and session-end messages, sent to SessionWatch's real hidden window,
are signalled while the sender waits (so a recording can be finished before Windows goes on).

Run with: QT_QPA_PLATFORM=windows python -m pytest -m desktop
"""
import ctypes
from ctypes import wintypes

import pytest
from PySide6.QtGui import QGuiApplication

from snipforge.video import power

pytestmark = pytest.mark.desktop


@pytest.fixture(scope="module", autouse=True)
def real_platform(qapp):
    if QGuiApplication.platformName() == "offscreen":
        pytest.skip("desktop tests need a real display: set QT_QPA_PLATFORM=windows")


def send(window: int, message: int, wparam: int) -> int:
    user32 = ctypes.WinDLL("user32")
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    return user32.SendMessageW(window, message, wparam, 0)


def test_sleep_and_session_end_are_signalled_while_windows_waits(qapp):
    watch = power.SessionWatch()
    heard = []
    watch.sleeping.connect(lambda: heard.append("sleep"))
    watch.ending_soon.connect(lambda: heard.append("ending?"))
    watch.not_ending.connect(lambda: heard.append("not ending"))
    try:
        assert watch.window_id

        send(watch.window_id, power.WM_POWERBROADCAST, power.PBT_APMSUSPEND)
        assert heard == ["sleep"]  # handled before SendMessage returned
        assert send(watch.window_id, power.WM_QUERYENDSESSION, 0) == 1  # yes, the session may end
        send(watch.window_id, power.WM_ENDSESSION, 0)

        assert heard == ["sleep", "ending?", "not ending"]
        watch.block_shutdown("Saving your screen recording")
        watch.allow_shutdown()
    finally:
        watch.close()
    assert watch.window_id == 0
