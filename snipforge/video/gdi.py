"""The screen area as it is right now, through GDI: a recording's first frame.

DXGI Desktop Duplication (video/dxgi.py) only hands over an image once something on the screen
changes, so a recording of a still screen would have nothing to show for its first frame. One GDI
copy fills that in. It's slower than DXGI (fine once) and, like it, leaves out windows hidden from
capture (SetWindowDisplayAffinity), such as the recording controls.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import Structure, byref, c_int, c_long, c_uint32, c_ushort, c_void_p
from ctypes import wintypes as w

from PySide6.QtGui import QImage

from .dpi import physical_pixels

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000  # include layered windows (menus, tooltips) as they look
DIB_RGB_COLORS = 0
BI_RGB = 0


class _BITMAPINFOHEADER(Structure):
    _fields_ = [("biSize", c_uint32), ("biWidth", c_long), ("biHeight", c_long), ("biPlanes", c_ushort),
                ("biBitCount", c_ushort), ("biCompression", c_uint32), ("biSizeImage", c_uint32),
                ("biXPelsPerMeter", c_long), ("biYPelsPerMeter", c_long), ("biClrUsed", c_uint32),
                ("biClrImportant", c_uint32)]


def _api():
    user32, gdi32 = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("gdi32", use_last_error=True)
    user32.GetDC.argtypes, user32.GetDC.restype = [w.HWND], w.HDC
    user32.ReleaseDC.argtypes = [w.HWND, w.HDC]
    gdi32.CreateCompatibleDC.argtypes, gdi32.CreateCompatibleDC.restype = [w.HDC], w.HDC
    gdi32.CreateDIBSection.argtypes = [w.HDC, c_void_p, w.UINT, ctypes.POINTER(c_void_p), w.HANDLE, w.DWORD]
    gdi32.CreateDIBSection.restype = w.HBITMAP
    gdi32.SelectObject.argtypes, gdi32.SelectObject.restype = [w.HDC, w.HGDIOBJ], w.HGDIOBJ
    gdi32.BitBlt.argtypes = [w.HDC, c_int, c_int, c_int, c_int, w.HDC, c_int, c_int, w.DWORD]
    gdi32.DeleteObject.argtypes = [w.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [w.HDC]
    return user32, gdi32


def grab_area(area: tuple[int, int, int, int]) -> QImage:
    """The area (x, y, width, height in physical desktop pixels) as a Format_RGB32 image."""
    if sys.platform != "win32":
        raise OSError("copying the screen this way needs Windows")
    x, y, width, height = area
    user32, gdi32 = _api()
    with physical_pixels():
        screen = user32.GetDC(None)
        memory = gdi32.CreateCompatibleDC(screen)
        header = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), width, -height, 1, 32, BI_RGB)
        bits = c_void_p()
        bitmap = gdi32.CreateDIBSection(memory, byref(header), DIB_RGB_COLORS, byref(bits), None, 0)
        previous = gdi32.SelectObject(memory, bitmap)
        try:
            if not bitmap or not gdi32.BitBlt(memory, 0, 0, width, height, screen, x, y, SRCCOPY | CAPTUREBLT):
                raise OSError(f"Couldn't copy the screen ({ctypes.get_last_error() or 'GDI refused'})")
            data = ctypes.string_at(bits, width * height * 4)
        finally:
            gdi32.SelectObject(memory, previous)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(memory)
            user32.ReleaseDC(None, screen)
    return QImage(data, width, height, width * 4, QImage.Format.Format_RGB32).copy()
