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
