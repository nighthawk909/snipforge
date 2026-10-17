"""Optional 'presentation' styling: padding, gradient backdrop, rounded corners, shadow.

Copy, Save, drag, Pin and Share add it; the editor previews it around the snip (crop.py) with the
same painting, so what you see is what you paste. One switch for the whole app: set_enabled()
saves the choice and tells every open editor and the Settings window.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath

from ..settings import app_settings

SETTING = "beautify"


@dataclass(frozen=True)
class BeautifyStyle:
    padding: int = 56
    radius: int = 14
    shadow: int = 28
    start: str = "#7f5af0"
    end: str = "#2cb1bc"


DEFAULT_STYLE = BeautifyStyle()


class _Switch(QObject):
    changed = Signal(bool)


_switch: _Switch | None = None


def switch() -> _Switch:
    """`switch().changed` fires with the new value. Connect a bound method of a QObject so the
    connection ends when that object is destroyed."""
    global _switch
    if _switch is None:
        _switch = _Switch()
    return _switch


def enabled() -> bool:
    return app_settings().value(SETTING, False, bool)


def set_enabled(on: bool) -> None:
    """Always tells everyone, even if the saved value already matches, so a switch can't get out
    of step with what Copy does (e.g. when the setting couldn't be saved)."""
    app_settings().setValue(SETTING, on)
    switch().changed.emit(on)


def card_path(card: QRectF, style: BeautifyStyle) -> QPainterPath:
    """The snip's outline once its corners are rounded."""
    path = QPainterPath()
    path.addRoundedRect(card, style.radius, style.radius)
    return path


def paint_backdrop(p: QPainter, card: QRectF, style: BeautifyStyle) -> None:
    """The gradient around `card` (the snip) and the shadow under it."""
    backdrop = card.adjusted(-style.padding, -style.padding, style.padding, style.padding)
    gradient = QLinearGradient(backdrop.topLeft(), backdrop.bottomRight())
    gradient.setColorAt(0, QColor(style.start))
    gradient.setColorAt(1, QColor(style.end))
    p.fillRect(backdrop, gradient)
    p.setPen(Qt.NoPen)
    for i in range(style.shadow, 0, -2):  # stacked translucent layers fake a blur
        p.setBrush(QColor(0, 0, 0, 5))
        p.drawRoundedRect(card.adjusted(-i, -i + 10, i, i + 10), style.radius + i, style.radius + i)


def backdrop_image(width: int, height: int, style: BeautifyStyle, scale: float = 1.0) -> QImage:
    """The backdrop and shadow around a `width` x `height` snip, with the snip's rounded card left
    clear. The editor draws this over the snip as its preview."""
    pad = style.padding
    out = QImage(math.ceil((width + pad * 2) * scale), math.ceil((height + pad * 2) * scale),
                 QImage.Format_ARGB32_Premultiplied)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(scale, scale)
    card = QRectF(pad, pad, width, height)
    paint_backdrop(p, card, style)
    p.setCompositionMode(QPainter.CompositionMode_Clear)
    p.fillPath(card_path(card, style), Qt.black)
    p.end()
    return out


def apply_beautify(image: QImage, style: BeautifyStyle = DEFAULT_STYLE) -> QImage:
    pad = style.padding
    out = QImage(image.width() + pad * 2, image.height() + pad * 2, QImage.Format_ARGB32_Premultiplied)
    p = QPainter(out)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    card = QRectF(pad, pad, image.width(), image.height())
    paint_backdrop(p, card, style)
    p.setClipPath(card_path(card, style))
    p.drawImage(card.topLeft(), image)
    p.end()
    return out
