"""Color native Windows title bars to match the theme (Windows 11; harmless elsewhere).

Keeping the real title bar (instead of drawing our own) keeps snapping, resizing and
accessibility exactly as Windows does them.
"""
from __future__ import annotations

import ctypes
import sys

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36


def colorref(hex_color: str) -> int:
    """'#RRGGBB' -> the 0x00BBGGRR integer Windows expects."""
    r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    return (b << 16) | (g << 8) | r


def _set(hwnd: int, attribute: int, value: int) -> bool:
    data = ctypes.c_int(value)
    result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
        ctypes.c_void_p(hwnd), ctypes.c_uint(attribute), ctypes.byref(data), ctypes.sizeof(data))
    return result == 0


def style_title_bar(hwnd: int, dark: bool, caption: str | None, text: str | None) -> bool:
    """Dark or light frame, plus caption and text colors where Windows supports them.
    Returns True if Windows accepted the dark/light setting."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        ok = _set(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, int(dark))
        if caption:
            _set(hwnd, DWMWA_CAPTION_COLOR, colorref(caption))
            _set(hwnd, DWMWA_BORDER_COLOR, colorref(caption))
        if text:
            _set(hwnd, DWMWA_TEXT_COLOR, colorref(text))
        return ok
    except (OSError, AttributeError, ValueError):
        return False
