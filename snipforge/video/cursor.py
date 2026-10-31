"""The pointer in recordings: its real shape, a highlight under it and a ring for each click,
drawn into every frame.

DXGI's frames never contain the pointer (video/dxgi.py), so the recorder draws it, which is what
lets it add a highlight and click rings. Each can be switched off.

Clicks are read by polling the mouse buttons once per frame (GetAsyncKeyState), and only while
click rings are on: no mouse hook, as the IT review section of docs/VIDEO-PLAN.md promises.

The pointer's image is made the way snips make it (snipforge.pointer: drawn on black and on white,
pointer.with_alpha works out the transparency). That's a per-pixel Python loop, too slow for every
frame, so each shape is made once and kept by its HCURSOR handle; prewarm() makes the standard
ones before recording starts.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen

from .. import pointer
from .dpi import physical_pixels

log = logging.getLogger(__name__)

HIGHLIGHT_RADIUS = 26
HIGHLIGHT_COLOUR = QColor(255, 214, 0, 90)  # a soft yellow
RING_SECONDS = 0.45  # how long a click ring grows and fades
RING_RADII = (6, 34)  # from, to
RING_WIDTH = 4
LEFT_RING = QColor(255, 140, 0)  # orange
RIGHT_RING = QColor(0, 150, 255)  # blue
SHAPE_CACHE_SIZE = 64  # apps that make a new pointer for every brush size can't fill memory
RETRY_SECONDS = 1.0  # a pointer that couldn't be drawn is tried again after this long, not every frame

_KEY_DOWN = 0x8000  # GetAsyncKeyState: the button is down now
# Pressed since the last poll: catches a click that fell between two frames, when Windows still has
# it (best effort: Microsoft keeps this bit for old programs, and another reader can take it first).
_KEY_PRESSED = 0x0001

# The standard pointers (IDC_ARROW first, then I-beam, busy, cross, up arrow, the resize arrows,
# "no", hand, working in the background and help), made before recording starts.
_STANDARD_CURSORS = (32512, 32513, 32514, 32515, 32516, 32642, 32643, 32644, 32645, 32646, 32648, 32649, 32650,
                     32651)


@dataclass(frozen=True)
class PointerEffects:
    """Which of the pointer, its highlight and click rings go into the recording."""
    pointer: bool = True
    highlight: bool = True
    clicks: bool = True

    @property
    def any(self) -> bool:
        return self.pointer or self.highlight or self.clicks


@dataclass(frozen=True)
class PointerShape:
    """A pointer ready to draw: its image (premultiplied ARGB), its hotspot within the image, and a
    mask that's white where the pointer inverts what's under it (the text I-beam), if anywhere."""
    image: QImage
    hotspot: tuple[int, int]
    inverted: QImage | None = None


@dataclass(frozen=True)
class PointerState:
    """Where the pointer's hotspot is (physical desktop pixels), whether it shows, and its HCURSOR."""
    x: int
    y: int
    visible: bool
    handle: int


@dataclass(frozen=True)
class Ring:
    """A click: when, where (physical desktop pixels) and whether it was the (logical) left button."""
    time: float
    x: int
    y: int
    left: bool


class PointerSource(Protocol):
    """Where the pointer and the mouse buttons are (Windows, or a fake in tests)."""

    def state(self) -> PointerState | None: ...
    def buttons(self) -> tuple[int, int]: ...  # GetAsyncKeyState of the physical left and right buttons
    def swapped(self) -> bool: ...  # SM_SWAPBUTTON: the buttons are swapped for a left-handed mouse
    def shape(self, handle: int) -> PointerShape | None: ...  # slow: kept by ShapeCache
    def standard_handles(self) -> tuple[int, ...]: ...


def shape_from_drawings(on_black: QImage, on_white: QImage, hotspot: tuple[int, int]) -> PointerShape:
    """A pointer drawn on black and on white (as snipforge._pointer_win32 draws it), ready to draw."""
    image, inverted = pointer.with_alpha(on_black, on_white)
    mask = None
    if inverted:
        mask = QImage(image.size(), QImage.Format_ARGB32_Premultiplied)
        mask.fill(Qt.transparent)
        for x, y in inverted:
            mask.setPixel(x, y, 0xFFFFFFFF)
    return PointerShape(image.convertToFormat(QImage.Format_ARGB32_Premultiplied), hotspot, mask)


class ShapeCache:
    """Pointer shapes by handle, made once each: the least recently used is forgotten past `limit`,
    and a pointer that couldn't be made is tried again after `retry_after` seconds."""

    def __init__(self, render: Callable[[int], PointerShape | None], limit: int = SHAPE_CACHE_SIZE,
                 retry_after: float = RETRY_SECONDS) -> None:
        self._render = render
        self._limit = limit
        self._retry_after = retry_after
        self._shapes: OrderedDict[int, tuple[PointerShape | None, float]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._shapes)

    def get(self, handle: int, *, now: float) -> PointerShape | None:
        cached = self._shapes.get(handle)
        if cached is not None and (cached[0] is not None or now - cached[1] < self._retry_after):
            self._shapes.move_to_end(handle)
            return cached[0]
        shape = self._render(handle)
        self._shapes[handle] = (shape, now)
        self._shapes.move_to_end(handle)
        while len(self._shapes) > self._limit:
            self._shapes.popitem(last=False)
        return shape


class _Clicks:
    """Turns the mouse buttons, polled once per frame, into click rings."""

    def __init__(self) -> None:
        self._down: tuple[bool, bool] | None = None  # None until the first poll
        self.rings: tuple[Ring, ...] = ()
        self.seen = 0

    def poll(self, now: float, position: tuple[int, int] | None, keys: tuple[int, int], swapped: bool) -> None:
        new = []
        if self._down is not None and position is not None:  # the first poll only notes the buttons
            for physical, (key, was_down) in enumerate(zip(keys, self._down, strict=True)):
                if (key & _KEY_DOWN and not was_down) or key & _KEY_PRESSED:
                    # GetAsyncKeyState reads the physical buttons; colour by what they mean.
                    new.append(Ring(now, *position, left=(physical == 0) != swapped))
        self._down = (bool(keys[0] & _KEY_DOWN), bool(keys[1] & _KEY_DOWN))
        self.seen += len(new)
        self.rings = tuple(ring for ring in (*self.rings, *new) if now - ring.time < RING_SECONDS)


class PointerOverlay:
    """Draws the pointer, its highlight and click rings into frames of the area (x, y, width,
    height in physical desktop pixels). A frame may be smaller than the area (the recorder scales
    big areas down): everything is scaled to fit.

    Call draw() once per frame, in order: it also polls the mouse buttons for clicks.
    """

    def __init__(self, area: tuple[int, int, int, int], effects: PointerEffects = PointerEffects(),
                 source: PointerSource | None = None) -> None:
        self.area = tuple(area)
        self.effects = effects
        self._source = source if source is not None else system_source()
        self._shapes = ShapeCache(self._source.shape)
        self._clicks = _Clicks()
        self._position: tuple[int, int] | None = None

    @property
    def clicks_seen(self) -> int:
        return self._clicks.seen

    def prewarm(self, now: float | None = None) -> None:
        """Makes the standard pointers' images now (during the countdown), not mid-recording."""
        if self.effects.pointer:
            for handle in self._source.standard_handles():
                self._shapes.get(handle, now=time.perf_counter() if now is None else now)

    def draw(self, frame: QImage, now: float) -> None:
        """Draws into `frame` (in place: copying every frame would cost more than drawing), as things
        are at `now` (time.perf_counter() seconds)."""
        if not self.effects.any:
            return
        state = self._source.state()
        if state is not None:
            self._position = (state.x, state.y)
        if self.effects.clicks:
            self._clicks.poll(now, self._position, self._source.buttons(), self._source.swapped())
        rings = self._clicks.rings if self.effects.clicks else ()
        showing = state is not None and state.visible
        shape = self._shapes.get(state.handle, now=now) if showing and self.effects.pointer else None
        highlight = showing and self.effects.highlight
        if not (rings or highlight or shape):
            return
        painter = QPainter(frame)
        try:
            self._place(painter, frame)
            painter.setRenderHint(QPainter.Antialiasing)
            for ring in rings:
                _draw_ring(painter, ring, now)
            if highlight:
                painter.setPen(Qt.NoPen)
                painter.setBrush(HIGHLIGHT_COLOUR)
                painter.drawEllipse(QPointF(state.x + 0.5, state.y + 0.5), HIGHLIGHT_RADIUS, HIGHLIGHT_RADIUS)
            if shape is not None:
                _draw_shape(painter, shape, state)
        finally:
            painter.end()

    def _place(self, painter: QPainter, frame: QImage) -> None:
        """Lets the painter work in desktop pixels: the area's top-left is the frame's."""
        x, y, width, height = self.area
        ratio = frame.devicePixelRatio()
        scale_x, scale_y = frame.width() / ratio / width, frame.height() / ratio / height
        if (scale_x, scale_y) != (1.0, 1.0):
            painter.scale(scale_x, scale_y)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)  # a shrunken pointer stays legible
        painter.translate(-x, -y)


def _draw_ring(painter: QPainter, ring: Ring, now: float) -> None:
    progress = (now - ring.time) / RING_SECONDS
    if not 0.0 <= progress < 1.0:
        return
    small, big = RING_RADII
    radius = small + (big - small) * progress
    colour = QColor(LEFT_RING if ring.left else RIGHT_RING)
    colour.setAlphaF(1.0 - progress)
    painter.setPen(QPen(colour, RING_WIDTH))
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(QPointF(ring.x + 0.5, ring.y + 0.5), radius, radius)


def _draw_shape(painter: QPainter, shape: PointerShape, state: PointerState) -> None:
    top_left = QPoint(state.x - shape.hotspot[0], state.y - shape.hotspot[1])
    painter.drawImage(top_left, shape.image)
    if shape.inverted is not None:
        painter.setCompositionMode(QPainter.CompositionMode_Difference)  # white: 255 - what's there
        painter.drawImage(top_left, shape.inverted)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)


def system_source() -> PointerSource:
    """This computer's pointer: Windows', or none elsewhere."""
    return _Win32Source() if sys.platform == "win32" else _NoPointer()


class _NoPointer:
    def state(self) -> PointerState | None:
        return None

    def buttons(self) -> tuple[int, int]:
        return 0, 0

    def swapped(self) -> bool:
        return False

    def shape(self, handle: int) -> PointerShape | None:
        return None

    def standard_handles(self) -> tuple[int, ...]:
        return ()


class _Win32Source:
    """GetCursorInfo for the pointer, GetAsyncKeyState for the buttons, and snipforge.pointer's
    drawing code for its image."""

    def __init__(self) -> None:
        from ctypes import wintypes

        from .. import _pointer_win32
        self._win = _pointer_win32
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetCursorInfo.argtypes = [ctypes.POINTER(_pointer_win32.CURSORINFO)]
        user32.GetCursorInfo.restype = wintypes.BOOL
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
        user32.LoadCursorW.restype = wintypes.HANDLE
        user32.CopyIcon.argtypes = [wintypes.HICON]
        user32.CopyIcon.restype = wintypes.HICON
        user32.DestroyIcon.argtypes = [wintypes.HICON]
        self._user32 = user32

    def state(self) -> PointerState | None:
        info = self._win.CURSORINFO(cbSize=ctypes.sizeof(self._win.CURSORINFO))
        with physical_pixels():  # otherwise a thread that isn't per-monitor DPI aware gets scaled positions
            if not self._user32.GetCursorInfo(ctypes.byref(info)):
                return None  # e.g. while the secure desktop (UAC prompt) is up
        handle = int(info.hCursor or 0)
        visible = bool(info.flags & self._win.CURSOR_SHOWING) and handle != 0
        return PointerState(info.ptScreenPos.x, info.ptScreenPos.y, visible, handle)

    def buttons(self) -> tuple[int, int]:
        return self._user32.GetAsyncKeyState(0x01), self._user32.GetAsyncKeyState(0x02)  # VK_LBUTTON, VK_RBUTTON

    def swapped(self) -> bool:
        return bool(self._user32.GetSystemMetrics(23))  # SM_SWAPBUTTON

    def shape(self, handle: int) -> PointerShape | None:
        icon = self._user32.CopyIcon(handle)  # keeps the pointer alive while it's drawn
        if not icon:
            log.debug("Pointer %#x is gone", handle)
            return None
        try:
            with physical_pixels():  # the pointer's size for the monitor, as Windows draws it
                hotspot, size = self._win._geometry(icon)
                on_black, on_white = (self._win._draw(icon, size, fill) for fill in (0x00, 0xFF))
        except (OSError, ValueError) as exc:
            log.warning("Couldn't draw pointer %#x: %s", handle, exc)
            return None
        finally:
            self._user32.DestroyIcon(icon)
        return shape_from_drawings(on_black, on_white, hotspot)

    def standard_handles(self) -> tuple[int, ...]:
        handles = (self._user32.LoadCursorW(None, ctypes.c_void_p(cursor_id)) for cursor_id in _STANDARD_CURSORS)
        return tuple(int(handle) for handle in handles if handle)
