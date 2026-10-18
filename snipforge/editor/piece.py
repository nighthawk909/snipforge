"""Smart Move: part of the snip picked up and put somewhere else, with Smart erase (erase.py) filling
in where it was. The piece is a copy of the snip there as it looked when it was picked up: smart
erases, blurs and pixelations already in it stay in it, so moving a piece never brings back
anything hidden. It's kept in the snip's document (as PNG), so it can be moved again later.
"""
from __future__ import annotations

import base64
import math

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF
from PySide6.QtGui import QImage, QPainter, QTransform

from .items import BoxItem
from .style import Style

MAX_PIECE_PIXELS = 4_000_000  # a piece bigger than this is kept at this size (it's drawn stretched)


def encode(image: QImage) -> str:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return base64.b64encode(bytes(data)).decode("ascii")


def decode(text) -> QImage:
    """The piece's picture; ValueError if it isn't one (the document keeps such a state unread)."""
    if not isinstance(text, str) or not text:
        raise ValueError("a moved piece without its picture")
    try:
        image = QImage.fromData(base64.b64decode(text, validate=True), "PNG")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"a moved piece's picture is damaged: {exc}") from exc
    if image.isNull():
        raise ValueError("a moved piece's picture is damaged")
    return image


def _within_limit(image: QImage) -> QImage:
    pixels = image.width() * image.height()
    if pixels <= MAX_PIECE_PIXELS:
        return image
    k = math.sqrt(MAX_PIECE_PIXELS / pixels)
    return image.scaled(max(1, int(image.width() * k)), max(1, int(image.height() * k)))


class PieceItem(BoxItem):
    """A moved part of the snip: move it, resize it (the picture stretches) or delete it."""

    kind = "piece"
    supports_shadow = False
    keep_square_with_shift = False

    def __init__(self, style: Style, image: QImage | None = None) -> None:
        self._image = QImage()
        self._encoded = ""
        super().__init__(style)
        if image is not None:
            self.set_image(image)

    def image(self) -> QImage:
        return self._image

    def set_image(self, image: QImage) -> None:
        self._image = _within_limit(image)
        self._encoded = encode(self._image)
        self.update()

    def _margin(self) -> float:
        return 1.0

    def _geometry(self) -> dict:
        return {**super()._geometry(), "image": self._encoded}

    def _set_geometry(self, state: dict) -> None:
        image = decode(state.get("image"))  # first: a damaged picture changes nothing
        super()._set_geometry(state)
        self._image, self._encoded = image, state["image"]

    def paint(self, p: QPainter, _opt, _widget=None) -> None:
        if not self._image.isNull():
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(self._rect, self._image)


def turned(text: str, transform: QTransform) -> str:
    """A piece's picture once the snip is turned or flipped by `transform` (its scale is left to
    the piece's new size)."""
    image = decode(text)
    sign = lambda v: 0 if abs(v) < 1e-9 else (1 if v > 0 else -1)  # noqa: E731 - turn and flip only
    linear = QTransform(sign(transform.m11()), sign(transform.m12()), sign(transform.m21()), sign(transform.m22()),
                        0, 0)
    if linear.isIdentity():
        return text
    return encode(image.transformed(linear))


def picked_up(source: QImage, area: QRectF) -> QImage:
    """The part of `source` (the snip as shown, hidden parts hidden) under `area`."""
    return source.copy(area.toAlignedRect().intersected(source.rect()))


def default_style() -> Style:
    return Style(shadow=False)
