"""The mouse pointer as an image, so a snip can include it.

Windows draws the pointer on top of the screen, so screenshots never contain it. grab() asks
Windows for the pointer as it looked when the screen was frozen: it draws it once on black and
once on white, and the difference gives each pixel's transparency. Old-style pointers that invert
what's under them (the text I-beam) do the same when drawn onto the frozen screen. Elsewhere grab()
returns None, until a Mac version fills it in.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage, QPainter

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PointerShot:
    image: QImage  # with transparency
    position: QPoint  # where its top-left sits on the screen, in physical pixels
    inverted: tuple[tuple[int, int], ...] = ()  # pixels (in `image`) that invert what's under them


def grab() -> PointerShot | None:
    """The pointer right now, or None (hidden, not Windows, or Windows wouldn't say)."""
    if sys.platform != "win32":
        return None
    try:
        return _grab_windows()
    except (OSError, ValueError) as exc:
        log.info("Couldn't get the pointer image: %s", exc)
        return None


def with_alpha(on_black: QImage, on_white: QImage) -> tuple[QImage, tuple[tuple[int, int], ...]]:
    """The pointer with real transparency, from the same pointer drawn on black and on white, and
    the pixels that invert what's under them (left clear in the image)."""
    if on_black.size() != on_white.size():
        raise ValueError("the two drawings must be the same size")
    out = QImage(on_black.size(), QImage.Format_ARGB32)
    inverted = []
    for y in range(out.height()):
        for x in range(out.width()):
            black, white = on_black.pixelColor(x, y), on_white.pixelColor(x, y)
            if white.green() < black.green():  # lighter on black than on white: it inverts
                inverted.append((x, y))
                out.setPixelColor(x, y, QColor(0, 0, 0, 0))
                continue
            alpha = 255 - (white.green() - black.green())
            if alpha <= 0:
                out.setPixelColor(x, y, QColor(0, 0, 0, 0))
                continue
            out.setPixelColor(x, y, QColor(*(min(255, round(c * 255 / alpha))
                                             for c in (black.red(), black.green(), black.blue())), alpha))
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
    image, inverted = with_alpha(on_black, on_white)
    return PointerShot(image, top_left, inverted)
