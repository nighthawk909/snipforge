"""Smart erase: hide something by filling its area in from what's around it.

The fill is a "pull-push" pyramid, done entirely with Qt's own image scaling (no NumPy or OpenCV):
the area and a thin ring of pixels around it are copied out, the area itself is made transparent,
the picture is halved again and again (transparent pixels don't count towards an average, because
the pixels are premultiplied), and then each level's holes are filled with the coarser level scaled
back up under it. Only the ring is used, so things near the area don't smear into it. Plain
backgrounds come back exactly plain and gradients smooth, in tens of milliseconds even on a 4K
snip. The erased pixels are cleared before anything is computed, so nothing of what you erased can
leak into the fill.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QImage, QPainter

from .items import BoxItem
from .style import Style

RING = 3  # pixels around the area it fills from
WORK = QImage.Format_RGBA64_Premultiplied  # 16 bits a channel, so a plain background comes back exact


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
        out = QImage(area.size(), QImage.Format_ARGB32_Premultiplied)
        out.fill(Qt.lightGray)
        return out
    patch = source.copy(outer).convertToFormat(WORK)
    hole = area.translated(-outer.topLeft())
    painter = QPainter(patch)
    painter.setCompositionMode(QPainter.CompositionMode_Clear)
    painter.fillRect(hole, Qt.transparent)
    painter.end()
    levels = [patch]
    while levels[-1].width() > 1 or levels[-1].height() > 1:  # pull: average down to one pixel
        top = levels[-1]
        levels.append(top.scaled(max(1, top.width() // 2), max(1, top.height() // 2), Qt.IgnoreAspectRatio,
                                 Qt.SmoothTransformation))
    filled = _opaque(levels[-1])
    for level in reversed(levels[:-1]):  # push: each level's known pixels over the coarser guess
        under = filled.scaled(level.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        painter = QPainter(under)
        painter.drawImage(0, 0, level)
        painter.end()
        filled = _opaque(under)
    return filled.copy(hole).convertToFormat(QImage.Format_ARGB32_Premultiplied)


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
        key = (area.x(), area.y(), area.width(), area.height())
        if self._cache is None or self._cache[0] != key:
            self._cache = (key, fill(self._base, area))
        p.drawImage(self.mapRectFromScene(QRectF(area)), self._cache[1])
