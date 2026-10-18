"""snipforge/video/_power_win32.py: what Windows' sleep and session-end messages mean for a recording.
The desktop suite sends the real messages to a real hidden window."""
import sys

import pytest

pytestmark = pytest.mark.windows
if sys.platform != "win32":
    pytest.skip("Windows' messages: Windows only", allow_module_level=True)

from snipforge.video import _power_win32 as win  # noqa: E402 - only once it's known to be Windows


@pytest.mark.parametrize("message, wparam, meaning", [
    (win.WM_POWERBROADCAST, win.PBT_APMSUSPEND, "sleep"),
    (win.WM_POWERBROADCAST, 0x0012, None),  # resumed: nothing to do
    (win.WM_QUERYENDSESSION, 0, "ending?"),
    (win.WM_ENDSESSION, 1, "ending"),
    (win.WM_ENDSESSION, 0, "not ending"),
    (0x0010, 0, None),  # WM_CLOSE
])
def test_windows_messages_mean_what_windows_says(message, wparam, meaning):
    assert win.classify(message, wparam) == meaning


@pytest.mark.parametrize("message, wparam, display, meaning", [
    (win.WM_POWERBROADCAST, win.PBT_POWERSETTINGCHANGE, 0, "display off"),
    (win.WM_POWERBROADCAST, win.PBT_POWERSETTINGCHANGE, 1, "display on"),
    (win.WM_POWERBROADCAST, win.PBT_POWERSETTINGCHANGE, 2, "display dimmed"),
    (win.WM_POWERBROADCAST, win.PBT_POWERSETTINGCHANGE, None, None),  # some other power setting
    (win.WM_WTSSESSION_CHANGE, win.WTS_SESSION_LOCK, None, "locked"),
    (win.WM_WTSSESSION_CHANGE, win.WTS_SESSION_UNLOCK, None, "unlocked"),
    (win.WM_WTSSESSION_CHANGE, 0x1, None, None),  # a console connected: nothing to do
])
def test_the_screen_and_the_lock_mean_what_windows_says(message, wparam, display, meaning):
    assert win.classify(message, wparam, display) == meaning


def setting(guid, value, length=4):
    import ctypes

    if isinstance(guid, bytes):
        guid = win.GUID.from_buffer_copy(guid)
    data = win.POWERBROADCAST_SETTING(guid, length, value)
    return data, ctypes.addressof(data)  # kept alive with its address


def test_the_structures_are_laid_out_as_windows_has_them():
    import ctypes
    import uuid

    assert ctypes.sizeof(win.GUID) == 16 and ctypes.sizeof(win.POWERBROADCAST_SETTING) == 24
    assert win.POWERBROADCAST_SETTING.DataLength.offset == 16 and win.POWERBROADCAST_SETTING.Data.offset == 20
    assert win.CONSOLE_DISPLAY_STATE == uuid.UUID("6FE69556-704A-47A0-8F24-C28D936FDA47").bytes_le


def test_a_bogus_pointer_from_some_other_program_cant_crash_the_recorder():
    assert win.display_state(1) is None and win.display_state(-5) is None


def test_the_screens_state_is_read_from_windows_setting_change():
    off, address = setting(win.CONSOLE_DISPLAY_STATE, 0)
    assert win.display_state(address) == 0
    dimmed, address = setting(win.CONSOLE_DISPLAY_STATE, 2)
    assert win.display_state(address) == 2


def test_other_power_settings_arent_taken_for_the_screen():
    other = win.GUID.from_buffer_copy(win.CONSOLE_DISPLAY_STATE)
    other.Data1 = 0x245D8541  # a different setting
    data, address = setting(other, 0)
    assert win.display_state(address) is None
    short, address = setting(win.CONSOLE_DISPLAY_STATE, 0, length=1)
    assert win.display_state(address) is None and win.display_state(0) is None


def test_staying_awake_is_asked_of_windows_and_given_back():
    assert win.stay_awake(True) is True
    assert win.stay_awake(False) is True  # this test's thread lets Windows rest again


def test_the_face_asks_windows_to_stay_awake_and_lets_it_rest():
    from snipforge.video import power

    assert power.stay_awake(True) is True
    assert power.stay_awake(False) is True
