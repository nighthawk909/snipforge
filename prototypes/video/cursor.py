"""The pointer, drawn into each frame ourselves: its real shape, a highlight and click rings.

DXGI frames never contain the pointer, which is what we want: drawing it ourselves lets the
recording add a highlight and click rings (and, later, hide or smooth it in the editor).

Clicks are found by polling GetAsyncKeyState once per frame, not with a mouse hook, so recording
adds no new hook for IT to review (docs/IT-REVIEW.md lists the one keyboard hook).
"""
from __future__ import annotations

import ctypes
import time
from ctypes import Structure, byref, sizeof
from ctypes import wintypes as w

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.GetCursorInfo.argtypes = [ctypes.c_void_p]
user32.DrawIconEx.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int, w.HANDLE, ctypes.c_int, ctypes.c_int, w.UINT,
                              w.HANDLE, w.UINT]
user32.GetIconInfo.argtypes = [w.HANDLE, ctypes.c_void_p]
gdi32.CreateDIBSection.argtypes = [w.HDC, ctypes.c_void_p, w.UINT, ctypes.c_void_p, w.HANDLE, w.DWORD]
gdi32.CreateDIBSection.restype = w.HBITMAP
gdi32.SelectObject.argtypes = [w.HDC, w.HANDLE]
gdi32.SelectObject.restype = w.HANDLE
gdi32.DeleteObject.argtypes = [w.HANDLE]
gdi32.GetObjectW.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p]

CURSOR_SHOWING = 1
DI_NORMAL = 3
VK_LBUTTON, VK_RBUTTON = 1, 2
SM_SWAPBUTTON = 23
RING_SECONDS = 0.45
RING_RADIUS = (6, 34)
HIGHLIGHT_RADIUS = 26
HIGHLIGHT = QColor(255, 214, 0, 90)
RING_LEFT = QColor(255, 140, 0)
RING_RIGHT = QColor(0, 150, 255)


class CURSORINFO(Structure):
    _fields_ = [("cbSize", w.DWORD), ("flags", w.DWORD), ("hCursor", w.HANDLE), ("pt", w.POINT)]


class ICONINFO(Structure):
    _fields_ = [("fIcon", w.BOOL), ("xHotspot", w.DWORD), ("yHotspot", w.DWORD), ("hbmMask", w.HBITMAP),
                ("hbmColor", w.HBITMAP)]


class BITMAP(Structure):
    _fields_ = [("bmType", w.LONG), ("bmWidth", w.LONG), ("bmHeight", w.LONG), ("bmWidthBytes", w.LONG),
                ("bmPlanes", w.WORD), ("bmBitsPixel", w.WORD), ("bmBits", ctypes.c_void_p)]


class BITMAPINFOHEADER(Structure):
    _fields_ = [("biSize", w.DWORD), ("biWidth", w.LONG), ("biHeight", w.LONG), ("biPlanes", w.WORD),
                ("biBitCount", w.WORD), ("biCompression", w.DWORD), ("biSizeImage", w.DWORD),
                ("biXPelsPerMeter", w.LONG), ("biYPelsPerMeter", w.LONG), ("biClrUsed", w.DWORD),
                ("biClrImportant", w.DWORD)]


def _render(hcursor) -> tuple[QImage, int, int] | None:
    """(image, hotspot x, hotspot y). Drawn over black and over white to recover alpha, which also
    covers old monochrome cursors that have no alpha of their own."""
    info = ICONINFO()
    if not user32.GetIconInfo(hcursor, byref(info)):
        return None
    try:
        bm = BITMAP()
        gdi32.GetObjectW(info.hbmColor or info.hbmMask, sizeof(bm), byref(bm))
        width = bm.bmWidth
        height = bm.bmHeight if info.hbmColor else bm.bmHeight // 2  # mask holds AND and XOR halves
        hot_x, hot_y = info.xHotspot, info.yHotspot
    finally:
        for h in (info.hbmMask, info.hbmColor):
            if h:
                gdi32.DeleteObject(h)
    shots = []
    for fill in (0x00, 0xFF):
        header = BITMAPINFOHEADER(sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        hdc = gdi32.CreateCompatibleDC(None)
        dib = gdi32.CreateDIBSection(hdc, byref(header), 0, byref(bits), None, 0)
        old = gdi32.SelectObject(hdc, dib)
        ctypes.memset(bits, fill, width * height * 4)
        user32.DrawIconEx(hdc, 0, 0, hcursor, 0, 0, 0, None, DI_NORMAL)
        shots.append(ctypes.string_at(bits, width * height * 4))
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(dib)
        gdi32.DeleteDC(hdc)
    on_black, on_white = shots
    out = bytearray(len(on_black))
    for i in range(0, len(out), 4):
        b, g, r = on_black[i], on_black[i + 1], on_black[i + 2]
        alpha = 255 - (on_white[i + 1] - g)
        if alpha > 255:  # an inverting (XOR) pixel, such as the I-beam: draw it black
            out[i:i + 4] = b"\x00\x00\x00\xff"
        else:
            alpha = max(alpha, 0)
            out[i:i + 4] = bytes((min(b, alpha), min(g, alpha), min(r, alpha), alpha))
    image = QImage(bytes(out), width, height, width * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()
    return image, hot_x, hot_y


class PointerOverlay:
    """Paints the pointer, its highlight and click rings onto frames of the area at (x, y)."""

    def __init__(self, x: int, y: int, *, highlight: bool = True, rings: bool = True) -> None:
        self.x, self.y = x, y
        self.highlight, self.rings = highlight, rings
        self._shapes: dict[int, tuple[QImage, int, int] | None] = {}
        self._down = {VK_LBUTTON: False, VK_RBUTTON: False}
        self._clicks: list[tuple[float, float, float, QColor]] = []  # (time, x, y, colour)
        self.clicks_seen = 0

    def _poll_clicks(self, now: float, px: float, py: float) -> None:
        swapped = bool(user32.GetSystemMetrics(SM_SWAPBUTTON))
        for vk in (VK_LBUTTON, VK_RBUTTON):
            state = user32.GetAsyncKeyState(vk)  # physical buttons, hence the swap check
            down = bool(state & 0x8000)
            if (down and not self._down[vk]) or (state & 1 and not down):  # pressed, or a click between polls
                is_left = (vk == VK_LBUTTON) != swapped
                self._clicks.append((now, px, py, RING_LEFT if is_left else RING_RIGHT))
                self.clicks_seen += 1
            self._down[vk] = down
        self._clicks = [c for c in self._clicks if now - c[0] < RING_SECONDS]

    def paint(self, frame: QImage, now: float | None = None) -> None:
        now = time.perf_counter() if now is None else now
        info = CURSORINFO(sizeof(CURSORINFO))
        if not user32.GetCursorInfo(byref(info)):
            return
        px, py = info.pt.x - self.x, info.pt.y - self.y
        self._poll_clicks(now, px, py)
        p = QPainter(frame)
        p.setRenderHint(QPainter.Antialiasing)
        if self.rings:
            for t, cx, cy, colour in self._clicks:
                k = (now - t) / RING_SECONDS
                radius = RING_RADIUS[0] + (RING_RADIUS[1] - RING_RADIUS[0]) * k
                ring = QColor(colour)
                ring.setAlphaF(max(0.0, 1.0 - k))
                p.setPen(QPen(ring, 4))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(cx, cy), radius, radius)
        if info.flags & CURSOR_SHOWING:
            if self.highlight:
                p.setPen(Qt.NoPen)
                p.setBrush(HIGHLIGHT)
                p.drawEllipse(QPointF(px, py), HIGHLIGHT_RADIUS, HIGHLIGHT_RADIUS)
            key = int(info.hCursor or 0)
            if key not in self._shapes:
                self._shapes[key] = _render(info.hCursor)
            shape = self._shapes[key]
            if shape:
                image, hx, hy = shape
                p.drawImage(int(px - hx), int(py - hy), image)
        p.end()
