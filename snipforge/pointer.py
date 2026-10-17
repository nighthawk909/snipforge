"""The mouse pointer as an image, so a snip can include it.

Windows draws the pointer on top of the screen, so screenshots never contain it. grab() asks
Windows to draw the pointer as it looks right now, once on black and once on white (cheap), and
the difference gives each pixel's transparency, worked out only if the snip includes the pointer.
Old-style pointers that invert what's under them (the text I-beam) do the same when drawn onto
the frozen screen. Elsewhere grab() returns None, until a Mac version fills it in.
"""
from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage, QPainter

log = logging.getLogger(__name__)


class PointerShot:
    """The pointer: its image (with transparency), where its top-left sat on the screen (physical
    pixels), and the pixels of the image that invert what's under them."""

    def __init__(self, image: QImage | None = None, position: QPoint | None = None,
                 inverted: tuple[tuple[int, int], ...] = (), *, drawings: tuple[QImage, QImage] | None = None):
        self.position = position if position is not None else QPoint()
        self._image = image
        self._inverted = tuple(inverted)
        self._drawings = drawings  # (on black, on white): turned into the image when first needed

    def _resolve(self) -> None:
        if self._image is None and self._drawings is not None:
            self._image, self._inverted = with_alpha(*self._drawings)
            self._drawings = None

    @property
    def image(self) -> QImage:
        self._resolve()
        return self._image

    @property
    def inverted(self) -> tuple[tuple[int, int], ...]:
        self._resolve()
        return self._inverted


def available() -> bool:
    """Whether this platform can provide the pointer at all (Windows, for now)."""
    return sys.platform == "win32"


def grab() -> PointerShot | None:
    """The pointer right now, or None (hidden, not Windows, or Windows wouldn't say). Never fails:
    including the pointer is optional, so it must never stop a snip."""
    if not available():
        return None
    try:
        return _grab_windows()
    except Exception as exc:  # noqa: BLE001 - optional feature; log and snip without it
        log.warning("Couldn't get the pointer image: %s", exc)
        return None


def with_alpha(on_black: QImage, on_white: QImage) -> tuple[QImage, tuple[tuple[int, int], ...]]:
    """The pointer with real transparency, from the same pointer drawn on black and on white, and
    the pixels that invert what's under them (left clear in the image)."""
    if on_black.size() != on_white.size():
        raise ValueError("the two drawings must be the same size")
    black = on_black.convertToFormat(QImage.Format_RGB32)
    white = on_white.convertToFormat(QImage.Format_RGB32)
    width, height = black.width(), black.height()
    out = QImage(width, height, QImage.Format_ARGB32)
    out.fill(0)
    # 32-bit rows have no padding, so each image is width * height pixels, 0xAARRGGBB.
    src_black = memoryview(black.constBits()).cast("I")
    src_white = memoryview(white.constBits()).cast("I")
    dst = memoryview(out.bits()).cast("I")
    inverted = []
    for i in range(width * height):
        b, w = src_black[i] & 0xFFFFFF, src_white[i] & 0xFFFFFF
        if b == w:  # the same on both: solid
            dst[i] = 0xFF000000 | b
            continue
        if b == 0 and w == 0xFFFFFF:  # the background showing through
            continue
        channels = [((b >> s) & 255, (w >> s) & 255) for s in (16, 8, 0)]
        if any(cw < cb for cb, cw in channels):  # lighter on black than on white somewhere: inverts
            inverted.append((i % width, i // width))
            continue
        alpha = 255 - round(sum(cw - cb for cb, cw in channels) / 3)
        if alpha <= 0:
            continue
        r, g, bl = (min(255, cb * 255 // alpha) for cb, _ in channels)
        dst[i] = (alpha << 24) | (r << 16) | (g << 8) | bl
    return out, tuple(inverted)


def composite(screen: QImage, shot: PointerShot, origin: QPoint) -> QImage:
    """A copy of one monitor's frozen screen with the pointer drawn on. `origin` is that monitor's
    top-left in physical screen pixels."""
    result = screen.copy()
    dpr = result.devicePixelRatio()
    result.setDevicePixelRatio(1.0)  # paint in the image's own pixels, whatever the monitor's scaling
    top_left = shot.position - origin
    p = QPainter(result)
    p.drawImage(top_left, shot.image)
    p.end()
    for x, y in shot.inverted:
        sx, sy = top_left.x() + x, top_left.y() + y
        if result.valid(sx, sy):
            c = result.pixelColor(sx, sy)
            result.setPixelColor(sx, sy, QColor(255 - c.red(), 255 - c.green(), 255 - c.blue(), c.alpha()))
    result.setDevicePixelRatio(dpr)
    return result


def _grab_windows() -> PointerShot | None:
    from . import _pointer_win32
    drawn = _pointer_win32.draw_pointer()
    if drawn is None:
        return None
    on_black, on_white, top_left = drawn
    return PointerShot(position=top_left, drawings=(on_black, on_white))
