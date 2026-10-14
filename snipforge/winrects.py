"""Visible top-level window rectangles, for click-to-capture-a-window in the overlay."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

from PySide6.QtCore import QRect

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
MIN_SIZE = 40
SKIP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_dwm = ctypes.WinDLL("dwmapi")
_ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = [_ENUMPROC, wintypes.LPARAM]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]


def _cloaked(hwnd) -> bool:
    value = ctypes.c_int(0)
    _dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
    return value.value != 0


def _frame(hwnd) -> QRect | None:
    rect = wintypes.RECT()
    if _dwm.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)):
        return None
    r = QRect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
    return r if r.width() >= MIN_SIZE and r.height() >= MIN_SIZE else None


def visible_windows() -> list[QRect]:
    """Window frames in physical pixels, topmost first. Excludes this process's own windows."""
    own_pid = os.getpid()
    found: list[QRect] = []
    name = ctypes.create_unicode_buffer(64)

    def callback(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd) or _cloaked(hwnd):
            return True
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == own_pid:
            return True
        _user32.GetClassNameW(hwnd, name, len(name))
        if name.value in SKIP_CLASSES:
            return True
        frame = _frame(hwnd)
        if frame is not None:
            found.append(frame)
        return True

    _user32.EnumWindows(_ENUMPROC(callback), 0)
    return found
