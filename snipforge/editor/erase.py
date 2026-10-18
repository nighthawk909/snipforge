"""Smart erase: hide something by filling its area in from what's around it.

The fill is a "pull-push" pyramid, done entirely with Qt's own image scaling (no NumPy or OpenCV):
the area and a thin ring of pixels around it are copied out, the area itself is made transparent,
the picture is halved again and again (transparent pixels don't count towards an average, because
the pixels are premultiplied), and then each level's holes are filled with the coarser level scaled
back up under it. Only the ring is used, so things near the area don't smear into it. Plain
backgrounds come back plain and gradients smooth. A big area is filled at up to MAX_WORK pixels and
scaled up (the fill is smooth anyway), so it stays quick even on a 4K snip. The erased pixels are
cleared before anything is computed or scaled, so nothing of what you erased can leak into the fill.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QImage, QPainter

from .items import BoxItem
from .style import Style

RING = 3  # pixels around the area it fills from
MAX_WORK = 2_000_000  # pixels it fills at full size; a bigger area is filled smaller and scaled up
WORK = QImage.Format_RGBA64_Premultiplied  # 16 bits a channel: a plain background comes back plain


def _opaque(image: QImage) -> QImage:
    """Premultiplied colors divided by their coverage and made opaque: the average of only the
    known pixels."""
    return image.convertToFormat(QImage.Format_RGBA64).convertToFormat(QImage.Format_RGBX64).convertToFormat(WORK)


def fill(source: QImage, area: QRect) -> QImage:
    """An image the size of `area` that fills it from the pixels around it in `source`."""
    area = area.intersected(source.rect())
    if area.isEmpty():
        return QImage()
    outer = area.adjusted(-RING, -RING, RING, RING).intersected(source.rect())
    if outer == area:  # nothing around it to fill from (the whole snip): plain grey
        return _grey(area.size())
    patch = source.copy(outer).convertToFormat(QImage.Format_ARGB32_Premultiplied)
    hole = area.translated(-outer.topLeft())
    _clear(patch, hole)  # first, before any scaling can mix erased pixels into the ring
    full_size = hole.size()
    pixels = outer.width() * outer.height()
    if pixels > MAX_WORK:  # a huge area: the fill is smooth, so working smaller loses nothing you'd see
        k = math.sqrt(MAX_WORK / pixels)
        patch = patch.scaled(max(1, round(outer.width() * k)), max(1, round(outer.height() * k)),
                             Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        left, top = math.floor(hole.x() * k), math.floor(hole.y() * k)
        hole = QRect(left, top, max(1, math.ceil((hole.x() + hole.width()) * k) - left),
                     max(1, math.ceil((hole.y() + hole.height()) * k) - top))
        _clear(patch, hole)
    levels = [patch.convertToFormat(WORK)]
    while levels[-1].width() > 1 or levels[-1].height() > 1:  # pull: average down to one pixel
        top = levels[-1]
        levels.append(top.scaled(max(1, top.width() // 2), max(1, top.height() // 2), Qt.IgnoreAspectRatio,
                                 Qt.SmoothTransformation))
    if levels[-1].pixelColor(0, 0).alpha() == 0:  # nothing to fill from (a transparent snip): plain grey
        return _grey(full_size)
    filled = _opaque(levels[-1])
    # Push: each level's known pixels over the coarser guess. Drawn over an opaque guess, the result
    # stays opaque. The finest level's hole is all guess, so it's just the last guess scaled up.
    for level in reversed(levels[1:-1]):
        filled = filled.scaled(level.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        painter = QPainter(filled)
        painter.drawImage(0, 0, level)
        painter.end()
    filled = filled.scaled(levels[0].size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    result = filled.copy(hole)
    if result.size() != full_size:
        result = result.scaled(full_size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return result.convertToFormat(QImage.Format_ARGB32_Premultiplied)


def _grey(size) -> QImage:
    out = QImage(size, QImage.Format_ARGB32_Premultiplied)
    out.fill(Qt.lightGray)
    return out


def _clear(image: QImage, rect: QRect) -> None:
    painter = QPainter(image)
    painter.setCompositionMode(QPainter.CompositionMode_Clear)
    painter.fillRect(rect, Qt.transparent)
    painter.end()


def bake(image: QImage, erases) -> QImage:
    """`image` with each erase's fill drawn in: the snip as it looks once erased. Blur, pixelate and
    text recognition start from this, so nothing erased comes back through them."""
    erases = [e for e in erases if not e.area().isEmpty()]
    if not erases:
        return image
    out = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    painter = QPainter(out)
    for item in sorted(erases, key=lambda e: e.zValue()):
        area = item.area()
        painter.drawImage(area.topLeft(), item.patch(area))
    painter.end()
    return out


class EraseItem(BoxItem):
    """A rectangle filled in from its surroundings; move or resize it like any box."""

    kind = "erase"
    supports_shadow = False
    keep_square_with_shift = False
    hides_content = True  # a magnifier shows the erased snip, not what was erased

    def __init__(self, style: Style, base: QImage) -> None:
        self._base = base
        self._cache: tuple[tuple, QImage] | None = None
        super().__init__(style)

    def set_base(self, base: QImage) -> None:
        """The snip was turned or resized: fill from the new pixels."""
        self._base = base
        self._cache = None
        self.update()

    def _margin(self) -> float:
        return 1.0

    def paint(self, p: QPainter, _opt, _widget=None) -> None:
        area = self.scene_rect().toAlignedRect().intersected(self._base.rect())
        if area.isEmpty():
            return
        p.drawImage(self.mapRectFromScene(QRectF(area)), self.patch(area))

    def patch(self, area: QRect | None = None) -> QImage:
        """The fill for `area` (the item's area on the snip by default), cached."""
        if area is None:
            area = self.scene_rect().toAlignedRect().intersected(self._base.rect())
        key = (area.x(), area.y(), area.width(), area.height())
        if self._cache is None or self._cache[0] != key:
            patch = fill(self._base, area)
            if patch.isNull() and not area.isEmpty():  # out of memory: cover it anyway, never show what's under
                patch = _grey(area.size())
            self._cache = (key, patch)
        return self._cache[1]

    def area(self) -> QRect:
        return self.scene_rect().toAlignedRect().intersected(self._base.rect())
