"""Windows: the pointer's position and real mouse-wheel input (for snipforge.scroll_input)."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QPoint

INPUT_MOUSE = 0
MOUSEEVENTF_WHEEL = 0x0800


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    # MOUSEINPUT is the largest member on 64-bit too, but pad to the documented size to be safe.
    _fields_ = [("mi", MOUSEINPUT), ("_pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_user32.GetCursorPos.restype = wintypes.BOOL
_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.SetCursorPos.restype = wintypes.BOOL
_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT


def cursor_pos() -> QPoint:
    point = wintypes.POINT()
    if not _user32.GetCursorPos(ctypes.byref(point)):
        raise ctypes.WinError(ctypes.get_last_error())
    return QPoint(point.x, point.y)


def set_cursor_pos(at: QPoint) -> None:
    if not _user32.SetCursorPos(at.x(), at.y()):
        raise ctypes.WinError(ctypes.get_last_error())


def wheel(delta: int) -> None:
    """One wheel event of `delta` (negative scrolls down), sent as real input."""
    event = INPUT(type=INPUT_MOUSE)
    event.u.mi = MOUSEINPUT(0, 0, ctypes.c_uint32(delta).value, MOUSEEVENTF_WHEEL, 0, 0)
    if _user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) != 1:
        raise ctypes.WinError(ctypes.get_last_error())
