"""snipforge/video/dpi.py: screen code works in physical pixels whatever DPI mode its thread is in."""
import ctypes
import sys

import pytest

from snipforge.video import dpi


def test_off_windows_it_changes_nothing(on_a_mac):
    on_a_mac(dpi)
    with dpi.physical_pixels():
        pass


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPI modes")
def test_an_unaware_thread_sees_physical_pixels_inside_and_its_own_mode_after():
    # DuplicateOutput1 is refused, and GetCursorInfo scales positions, unless the thread is
    # per-monitor DPI aware; Qt makes the process so, but a thread or process may not be.
    user32 = ctypes.WinDLL("user32")
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]

    def awareness() -> int:  # 0 unaware, 1 system aware, 2 per monitor
        return user32.GetAwarenessFromDpiAwarenessContext(user32.GetThreadDpiAwarenessContext())

    previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-1))  # DPI_AWARENESS_CONTEXT_UNAWARE
    try:
        with dpi.physical_pixels():
            assert awareness() == 2
        assert awareness() == 0
    finally:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))
