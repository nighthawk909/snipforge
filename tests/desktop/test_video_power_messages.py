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


def send(window: int, message: int, wparam: int, lparam: int = 0) -> int:
    user32 = ctypes.WinDLL("user32")
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    return user32.SendMessageW(window, message, wparam, lparam)


def top_level_windows() -> list[int]:
    """Every top-level window, hidden ones too (EnumWindows leaves out message-only windows)."""
    found = []
    each = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(
        lambda hwnd, _param: found.append(hwnd or 0) or True)
    ctypes.WinDLL("user32").EnumWindows(each, 0)
    return found


def test_sleep_and_session_end_are_signalled_while_windows_waits(qapp):
    watch = power.SessionWatch()
    heard = []
    watch.sleeping.connect(lambda: heard.append("sleep"))
    watch.ending_soon.connect(lambda: heard.append("ending?"))
    watch.not_ending.connect(lambda: heard.append("not ending"))
    try:
        assert watch.window_id in top_level_windows()  # not message-only: Windows' broadcasts reach it

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


def test_the_screen_and_the_lock_are_signalled_and_windows_agreed_to_tell(qapp):
    watch = power.SessionWatch()
    heard = []
    for name in ("display_off", "display_on", "locked", "unlocked"):
        getattr(watch, name).connect(lambda name=name: heard.append(name))
    try:
        assert watch._display_note and watch._session_note  # Windows will send these for real
        off = power.POWERBROADCAST_SETTING(power.CONSOLE_DISPLAY_STATE, 4, 0)
        on = power.POWERBROADCAST_SETTING(power.CONSOLE_DISPLAY_STATE, 4, 1)

        send(watch.window_id, power.WM_POWERBROADCAST, power.PBT_POWERSETTINGCHANGE, ctypes.addressof(off))
        send(watch.window_id, power.WM_WTSSESSION_CHANGE, power.WTS_SESSION_LOCK)
        send(watch.window_id, power.WM_WTSSESSION_CHANGE, power.WTS_SESSION_UNLOCK)
        send(watch.window_id, power.WM_POWERBROADCAST, power.PBT_POWERSETTINGCHANGE, ctypes.addressof(on))

        assert heard[-4:] == ["display_off", "locked", "unlocked", "display_on"]
    finally:
        watch.close()
    assert watch._display_note is None and watch._session_note is False
