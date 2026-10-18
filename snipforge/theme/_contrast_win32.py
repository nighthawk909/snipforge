"""Windows' contrast-theme colors (GetSysColor), for contrast.py. Only imported on Windows."""
from __future__ import annotations

import ctypes

from .contrast import SystemColors

COLOR_WINDOW, COLOR_WINDOWTEXT, COLOR_HIGHLIGHT, COLOR_HIGHLIGHTTEXT = 5, 8, 13, 14
COLOR_BTNFACE, COLOR_GRAYTEXT, COLOR_BTNTEXT, COLOR_HOTLIGHT = 15, 17, 18, 26

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetSysColor.argtypes = [ctypes.c_int]
_user32.GetSysColor.restype = ctypes.c_uint32


def _hex(index: int) -> str:
    bgr = _user32.GetSysColor(index)  # 0x00BBGGRR
    return f"#{bgr & 0xFF:02X}{(bgr >> 8) & 0xFF:02X}{(bgr >> 16) & 0xFF:02X}"


def colors() -> SystemColors:
    try:
        return SystemColors(window=_hex(COLOR_WINDOW), text=_hex(COLOR_WINDOWTEXT), highlight=_hex(COLOR_HIGHLIGHT),
                            highlight_text=_hex(COLOR_HIGHLIGHTTEXT), button=_hex(COLOR_BTNFACE),
                            button_text=_hex(COLOR_BTNTEXT), disabled=_hex(COLOR_GRAYTEXT), link=_hex(COLOR_HOTLIGHT))
    except (AttributeError, ValueError) as exc:
        raise OSError(f"GetSysColor failed: {exc}") from exc
