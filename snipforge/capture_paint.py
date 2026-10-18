"""What the capture overlay paints over the frozen screen besides the dimming: the hint at the
bottom, the selection's size, and the magnifier that shows exact pixels under the pointer."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen

from . import theme
from .fonts import mono_font, ui_font

HINT_BOTTOM = 40  # gap between the hint and the bottom of the monitor
MAG_SAMPLE = 15  # physical pixels sampled around the cursor
MAG_SIZE = 120


def paint_hint(p: QPainter, size: QSize, text: str) -> None:
    """`text` in a dark pill near the bottom of a monitor of `size`."""
    p.setFont(ui_font(10))
    w = p.fontMetrics().horizontalAdvance(text) + 28
    box = QRectF((size.width() - w) / 2, size.height() - HINT_BOTTOM - 32, w, 32)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(20, 20, 20, 225))
    p.drawRoundedRect(box, 8, 8)
    p.setPen(QColor("#f0f0f0"))
    p.drawText(box, Qt.AlignCenter, text)


def paint_size_label(p: QPainter, sel: QRect, text: str) -> None:
    """The selection's size, just above it (or just inside, at the top of the screen)."""
    p.setFont(ui_font(9))
    w = p.fontMetrics().horizontalAdvance(text) + 14
    h = p.fontMetrics().height() + 8
    y = sel.top() - h - 6 if sel.top() > h + 10 else sel.top() + 6
    box = QRectF(sel.left(), y, w, h)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(20, 20, 20, 230))
    p.drawRoundedRect(box, 5, 5)
    p.setPen(Qt.white)
    p.drawText(box, Qt.AlignCenter, text)


def paint_magnifier(p: QPainter, image: QImage, dpr: float, pos: QPoint, size: QSize) -> None:
    """The physical pixels of `image` around `pos` (overlay coordinates), enlarged beside the
    pointer, with the pixel's position and colour."""
    px, py = round(pos.x() * dpr), round(pos.y() * dpr)
    half = MAG_SAMPLE // 2
    sample = image.copy(px - half, py - half, MAG_SAMPLE, MAG_SAMPLE)
    x = pos.x() + 24 if pos.x() + 24 + MAG_SIZE < size.width() else pos.x() - 24 - MAG_SIZE
    y = pos.y() + 24 if pos.y() + 24 + MAG_SIZE + 22 < size.height() else pos.y() - 24 - MAG_SIZE - 22
    target = QRect(x, y, MAG_SIZE, MAG_SIZE)
    p.setRenderHint(QPainter.SmoothPixmapTransform, False)
    p.drawImage(target, sample)
    cell = MAG_SIZE / MAG_SAMPLE
    p.setPen(QPen(QColor(theme.current().accent.fill), 1))
    p.setBrush(Qt.NoBrush)
    p.drawRect(QRectF(x + half * cell, y + half * cell, cell, cell))
    p.setPen(QPen(QColor("#ffffff"), 1))
    p.drawRect(target.adjusted(0, 0, -1, -1))
    p.setPen(QPen(QColor(0, 0, 0, 160), 1))
    p.drawRect(target.adjusted(-1, -1, 0, 0))
    color = image.pixelColor(px, py) if image.rect().contains(px, py) else QColor()
    label = QRectF(x, y + MAG_SIZE, MAG_SIZE, 22)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(20, 20, 20, 235))
    p.drawRect(label)
    p.setPen(QColor("#f0f0f0"))
    p.setFont(mono_font(8))
    p.drawText(label, Qt.AlignCenter, f"{px},{py}  {color.name().upper() if color.isValid() else ''}")
