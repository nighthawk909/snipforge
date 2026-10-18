"""Smart Move: part of the snip picked up and put somewhere else, with Smart erase (erase.py) filling
in where it was. The piece is a copy of the snip there as it looked when it was picked up: smart
erases, blurs and pixelations already in it stay in it, so moving a piece never brings back
anything hidden. It's kept in the snip's document (as PNG), so it can be moved again later.
"""
from __future__ import annotations

import base64
import math

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QImage, QImageReader, QPainter, QTransform

from . import cutout
from .items import BoxItem
from .style import Style

MAX_PIECE_PIXELS = 4_000_000  # a piece bigger than this is kept at this size (it's drawn stretched)
MAX_ENCODED = 40_000_000  # characters of base64: far more than a MAX_PIECE_PIXELS PNG ever takes
PNG_HEAD = b"\x89PNG\r\n\x1a\n"


def pixels(text) -> int | None:
    """How many pixels a piece's picture unpacks to, read from its PNG header without unpacking
    it; None if it isn't a PNG."""
    if not isinstance(text, str):
        return None
    try:
        head = base64.b64decode(text[:32], validate=True)  # 24 bytes: the signature and IHDR's size
    except (ValueError, TypeError):
        return None
    if len(head) < 24 or not head.startswith(PNG_HEAD) or head[12:16] != b"IHDR":
        return None
    return int.from_bytes(head[16:20], "big") * int.from_bytes(head[20:24], "big")


def encode(image: QImage) -> str:
    """The picture as base64 PNG; ValueError if it can't be (out of memory, say)."""
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    saved = image.save(buffer, "PNG")
    buffer.close()
    if not saved or data.isEmpty():
        raise ValueError("couldn't keep the moved piece's picture")
    return base64.b64encode(bytes(data)).decode("ascii")


def decode(text) -> QImage:
    """The piece's picture; ValueError if it isn't one, or is bigger than a piece ever is (the
    document keeps such a state unread, and a paste of it is refused)."""
    if not isinstance(text, str) or not text:
        raise ValueError("a moved piece without its picture")
    if len(text) > MAX_ENCODED:
        raise ValueError("a moved piece's picture is too big")
    try:
        data = QByteArray(base64.b64decode(text, validate=True))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"a moved piece's picture is damaged: {exc}") from exc
    buffer = QBuffer(data)
    buffer.open(QIODevice.ReadOnly)
    reader = QImageReader(buffer, b"PNG")
    size = reader.size()  # from the header, before anything is unpacked
    if not size.isValid() or size.width() * size.height() > MAX_PIECE_PIXELS:
        raise ValueError("a moved piece's picture is damaged or too big")
    image = reader.read()
    buffer.close()
    if image.isNull():
        raise ValueError("a moved piece's picture is damaged")
    return image


def _within_limit(image: QImage) -> QImage:
    pixels = image.width() * image.height()
    if pixels <= MAX_PIECE_PIXELS:
        return image
    k = math.sqrt(MAX_PIECE_PIXELS / pixels)
    return image.scaled(max(1, int(image.width() * k)), max(1, int(image.height() * k)),
                        Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


class PieceItem(BoxItem):
    """A moved part of the snip: move it, resize it (the picture stretches) or delete it."""

    kind = "piece"
    is_snip = True  # part of how the snip looks: Grab text and Edit text read it, Smart Move picks it up
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


def cut_out(text: str, rect: QRectF, cut: cutout.Cut) -> str:
    """A piece's picture once `cut` (snip pixels) takes its strip out of the snip: the part of the
    piece over the strip goes too. Nothing left: a transparent picture (the piece is then tiny)."""
    image = decode(text)
    horizontal = cut.axis == "x"
    low, length = (rect.left(), rect.width()) if horizontal else (rect.top(), rect.height())
    pixels = image.width() if horizontal else image.height()
    if length <= 0:
        return text
    start = max(0.0, cut.start - low) * pixels / length
    end = min(length, cut.end - low) * pixels / length
    first, last = max(0, math.floor(start)), min(pixels, math.ceil(end))  # outward: none of the strip stays
    if last <= first:
        return text  # the strip misses the piece
    if last - first >= pixels:
        empty = QImage(1, 1, QImage.Format_ARGB32_Premultiplied)
        empty.fill(Qt.transparent)
        return encode(empty)
    return encode(cutout.apply(image, cutout.Cut(cut.axis, first, last)))


def default_style() -> Style:
    return Style(shadow=False)
