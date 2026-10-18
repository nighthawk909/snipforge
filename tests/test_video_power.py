"""snipforge/video/power.py: what Windows' sleep and session-end messages mean for a recording.
The desktop suite sends the real messages to a real hidden window."""
import pytest

from snipforge.video import power


@pytest.mark.parametrize("message, wparam, meaning", [
    (power.WM_POWERBROADCAST, power.PBT_APMSUSPEND, "sleep"),
    (power.WM_POWERBROADCAST, 0x0012, None),  # resumed: nothing to do
    (power.WM_QUERYENDSESSION, 0, "ending?"),
    (power.WM_ENDSESSION, 1, "ending"),
    (power.WM_ENDSESSION, 0, "not ending"),
    (0x0010, 0, None),  # WM_CLOSE
])
def test_windows_messages_mean_what_windows_says(message, wparam, meaning):
    assert power.classify(message, wparam) == meaning


@pytest.mark.parametrize("message, wparam, display, meaning", [
    (power.WM_POWERBROADCAST, power.PBT_POWERSETTINGCHANGE, 0, "display off"),
    (power.WM_POWERBROADCAST, power.PBT_POWERSETTINGCHANGE, 1, "display on"),
    (power.WM_POWERBROADCAST, power.PBT_POWERSETTINGCHANGE, 2, "display dimmed"),
    (power.WM_POWERBROADCAST, power.PBT_POWERSETTINGCHANGE, None, None),  # some other power setting
    (power.WM_WTSSESSION_CHANGE, power.WTS_SESSION_LOCK, None, "locked"),
    (power.WM_WTSSESSION_CHANGE, power.WTS_SESSION_UNLOCK, None, "unlocked"),
    (power.WM_WTSSESSION_CHANGE, 0x1, None, None),  # a console connected: nothing to do
])
def test_the_screen_and_the_lock_mean_what_windows_says(message, wparam, display, meaning):
    assert power.classify(message, wparam, display) == meaning


def setting(guid, value, length=4):
    import ctypes

    data = power.POWERBROADCAST_SETTING(guid, length, value)
    return data, ctypes.addressof(data)  # kept alive with its address


def test_the_screens_state_is_read_from_windows_setting_change():
    off, address = setting(power.CONSOLE_DISPLAY_STATE, 0)
    assert power.display_state(address) == 0
    dimmed, address = setting(power.CONSOLE_DISPLAY_STATE, 2)
    assert power.display_state(address) == 2


def test_other_power_settings_arent_taken_for_the_screen():
    other = power.GUID(0x245D8541, 0x3943, 0x4422, power.CONSOLE_DISPLAY_STATE.Data4)  # a different setting
    data, address = setting(other, 0)
    assert power.display_state(address) is None
    short, address = setting(power.CONSOLE_DISPLAY_STATE, 0, length=1)
    assert power.display_state(address) is None and power.display_state(0) is None


def test_staying_awake_is_asked_of_windows_and_given_back(monkeypatch):
    import sys

    if sys.platform != "win32":
        assert power.stay_awake(True) is False
        return
    assert power.stay_awake(True) is True
    assert power.stay_awake(False) is True  # this test's thread lets Windows rest again
