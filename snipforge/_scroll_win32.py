"""Windows: the pointer's position, real mouse-wheel input, and what's under the pointer (for
snipforge.scroll_input)."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QPoint

INPUT_MOUSE = 0
MOUSEEVENTF_WHEEL = 0x0800
GA_ROOT = 2
VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN = 0x10, 0x11, 0x12, 0x5B, 0x5C
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}  # desktop and taskbars


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]  # the largest member of the real union on 32- and 64-bit Windows


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_user32.GetCursorPos.restype = wintypes.BOOL
_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.SetCursorPos.restype = wintypes.BOOL
_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short
_user32.WindowFromPoint.argtypes = [wintypes.POINT]
_user32.WindowFromPoint.restype = wintypes.HWND
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetAncestor.restype = wintypes.HWND
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]


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


def modifiers_down() -> bool:
    return any(_user32.GetAsyncKeyState(vk) & 0x8000 for vk in (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN))


def window_at(at: QPoint) -> int:
    """The top-level window under `at` (0 if none)."""
    hwnd = _user32.WindowFromPoint(wintypes.POINT(at.x(), at.y()))
    root = _user32.GetAncestor(hwnd, GA_ROOT) if hwnd else None
    return int(root or 0)


def is_shell(window: int) -> bool:
    if not window:
        return True
    name = ctypes.create_unicode_buffer(64)
    _user32.GetClassNameW(window, name, 64)
    return name.value in SHELL_CLASSES
