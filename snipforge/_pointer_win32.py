"""Windows: draw the current mouse pointer into images (for snipforge.pointer)."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QPoint
from PySide6.QtGui import QImage

CURSOR_SHOWING = 0x1
DI_NORMAL = 0x3
DIB_RGB_COLORS = 0
BI_RGB = 0
MAX_SIZE = 512  # the largest accessibility pointer is 256 px; anything past this is bogus


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hCursor", wintypes.HANDLE),
                ("ptScreenPos", wintypes.POINT)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP)]


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG), ("bmHeight", wintypes.LONG),
                ("bmWidthBytes", wintypes.LONG), ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                ("bmBits", ctypes.c_void_p)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
_user32.GetCursorInfo.restype = wintypes.BOOL
_user32.CopyIcon.argtypes = [wintypes.HICON]
_user32.CopyIcon.restype = wintypes.HICON
_user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(ICONINFO)]
_user32.GetIconInfo.restype = wintypes.BOOL
_user32.DestroyIcon.argtypes = [wintypes.HICON]
_user32.DrawIconEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HICON, ctypes.c_int,
                               ctypes.c_int, wintypes.UINT, wintypes.HBRUSH, wintypes.UINT]
_user32.DrawIconEx.restype = wintypes.BOOL
_gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
_gdi32.GetObjectW.restype = ctypes.c_int
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT,
                                    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
_gdi32.CreateDIBSection.restype = wintypes.HBITMAP
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteDC.argtypes = [wintypes.HDC]


def draw_pointer() -> tuple[QImage, QImage, QPoint] | None:
    """(the pointer drawn on black, on white, its top-left on screen in physical pixels), or None
    when no pointer is showing."""
    info = CURSORINFO(cbSize=ctypes.sizeof(CURSORINFO))
    if not _user32.GetCursorInfo(ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not info.flags & CURSOR_SHOWING or not info.hCursor:
        return None
    icon = _user32.CopyIcon(info.hCursor)
    if not icon:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        (hot_x, hot_y), size = _geometry(icon)
        on_black, on_white = (_draw(icon, size, fill) for fill in (0x00, 0xFF))
    finally:
        _user32.DestroyIcon(icon)
    return on_black, on_white, QPoint(info.ptScreenPos.x - hot_x, info.ptScreenPos.y - hot_y)


def _geometry(icon) -> tuple[tuple[int, int], tuple[int, int]]:
    """((hotspot x, y), (width, height)) of a pointer."""
    parts = ICONINFO()
    if not _user32.GetIconInfo(icon, ctypes.byref(parts)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        bitmap = BITMAP()
        if not _gdi32.GetObjectW(parts.hbmColor or parts.hbmMask, ctypes.sizeof(BITMAP), ctypes.byref(bitmap)):
            raise OSError("couldn't read the pointer's size")
        width, height = bitmap.bmWidth, bitmap.bmHeight
        if not parts.hbmColor:
            height //= 2  # a black-and-white pointer stacks its two masks in one bitmap
        if not (0 < width <= MAX_SIZE and 0 < height <= MAX_SIZE):
            raise ValueError(f"unexpected pointer size {width}x{height}")
        return (parts.xHotspot, parts.yHotspot), (width, height)
    finally:
        for bitmap_handle in (parts.hbmMask, parts.hbmColor):
            if bitmap_handle:
                _gdi32.DeleteObject(bitmap_handle)


def _draw(icon, size: tuple[int, int], fill: int) -> QImage:
    """The pointer drawn onto a background of `fill` (0x00 black, 0xFF white)."""
    width, height = size
    header = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=width, biHeight=-height,
                              biPlanes=1, biBitCount=32, biCompression=BI_RGB)  # negative height: top-down
    bits = ctypes.c_void_p()
    dc = _gdi32.CreateCompatibleDC(None)
    if not dc:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        bitmap = _gdi32.CreateDIBSection(dc, ctypes.byref(header), DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
        if not bitmap or not bits.value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            previous = _gdi32.SelectObject(dc, bitmap)
            ctypes.memset(bits, fill, width * height * 4)
            drawn = _user32.DrawIconEx(dc, 0, 0, icon, width, height, 0, None, DI_NORMAL)
            _gdi32.SelectObject(dc, previous)
            if not drawn:
                raise ctypes.WinError(ctypes.get_last_error())
            data = ctypes.string_at(bits, width * height * 4)
            return QImage(data, width, height, width * 4, QImage.Format_RGB32).copy()
        finally:
            _gdi32.DeleteObject(bitmap)
    finally:
        _gdi32.DeleteDC(dc)
