"""Physical pixels for recording's screen and pointer code, whatever DPI mode a thread is in.

Qt makes the process per-monitor DPI aware (v2), and recording works in physical pixels. But a
thread can be in another mode (or a process can be started without Qt), and then Windows quietly
changes the rules: DXGI refuses DuplicateOutput1 (DXGI_ERROR_UNSUPPORTED, which reads like a driver
problem) and GetCursorInfo scales the pointer's position. So screen calls run inside
physical_pixels(), which switches only the calling thread, and only for as long as it takes.
"""
from __future__ import annotations

import ctypes
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache

_PER_MONITOR_AWARE_V2 = -4  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2


@contextmanager
def physical_pixels() -> Iterator[None]:
    """Runs the block with this thread per-monitor DPI aware (v2), then puts its mode back."""
    set_context = _set_context() if sys.platform == "win32" else None
    previous = set_context(ctypes.c_void_p(_PER_MONITOR_AWARE_V2)) if set_context else None
    try:
        yield
    finally:
        if previous:  # None or 0: nothing was changed (not Windows, or Windows 10 before 1607)
            set_context(ctypes.c_void_p(previous))


@cache
def _set_context():
    """SetThreadDpiAwarenessContext, or None on Windows versions without it."""
    function = getattr(ctypes.WinDLL("user32"), "SetThreadDpiAwarenessContext", None)
    if function is not None:
        function.argtypes = [ctypes.c_void_p]
        function.restype = ctypes.c_void_p
    return function
