"""The loupe: the pixels around a point, enlarged into crisp squares, with the one you'd pick outlined
and its color written underneath. The editor's eyedropper (editor/eyedropper.py) and Pick a color
(color_picker.py) both show it."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen

LABEL_GAP = 6  # between the pixels and the label
LABEL_H = 24
BACKDROP = QColor("#3a3a3c")  # behind anything outside the picture
GRID = QColor(128, 128, 128, 90)  # mid-gray, seen on light and dark pixels alike


def side(radius: int, cell: int) -> int:
    return (2 * radius + 1) * cell


def size(radius: int, cell: int) -> QSize:
    """The loupe's size, label included."""
    return QSize(side(radius, cell) + 2, side(radius, cell) + 2 + LABEL_GAP + LABEL_H)


def sample(source: QImage, x: int, y: int, radius: int) -> QImage:
    """The (2 * radius + 1)-pixel square around (x, y); outside `source` is transparent."""
    n = 2 * radius + 1
    area = QRect(x - radius, y - radius, n, n)
    if source.hasAlphaChannel() or source.rect().contains(area):
        return source.copy(area)  # (an opaque picture's copy would be black outside it)
    out = QImage(n, n, QImage.Format_ARGB32_Premultiplied)
    out.fill(Qt.transparent)
    inside = area.intersected(source.rect())
    if not inside.isEmpty():
        part = source.copy(inside)
        part.setDevicePixelRatio(1.0)
        p = QPainter(out)
        p.drawImage(inside.topLeft() - area.topLeft(), part)
        p.end()
    return out


def _cell(cell: int, ratio: float) -> float:
    """A cell's size, as drawn: whole device pixels (12 at 125%, not 12.5), so every cell is the same."""
    return max(1, round(cell * ratio)) / ratio


def cell_rect(top_left: QPoint, dx: int, dy: int, *, radius: int, cell: int, ratio: float = 1.0) -> QRectF:
    """Where the pixel `dx`, `dy` from the middle one is drawn, in the coordinates `top_left` is in."""
    step = _cell(cell, ratio)
    return QRectF(top_left.x() + 1 + (radius + dx) * step, top_left.y() + 1 + (radius + dy) * step, step, step)


def paint(p: QPainter, top_left: QPoint, pixels: QImage | None, label: str, *, radius: int, cell: int,
          ratio: float = 1.0, font: QFont | None = None, grid: bool = False, swatch: QColor | None = None) -> None:
    """The loupe with its top-left at `top_left`: `pixels` (from `sample`) enlarged, the middle one
    outlined, and `label` underneath (beside a square of `swatch`, if given). `ratio`: the device
    pixel ratio painted at. `grid`: lines between the pixels."""
    n = 2 * radius + 1
    whole = side(radius, cell)
    p.save()
    p.translate(top_left)
    p.setBrush(Qt.NoBrush)  # the outlines below are only outlines, whatever was painted before
    p.fillRect(QRect(1, 1, whole, whole), BACKDROP)
    step = cell
    if pixels is not None:
        step = _cell(cell, ratio)
        device_cell = round(step * ratio)
        enlarged = pixels.scaled(n * device_cell, n * device_cell, Qt.IgnoreAspectRatio,
                                 Qt.FastTransformation)  # no smoothing: crisp squares
        enlarged.setDevicePixelRatio(ratio)
        p.drawImage(QPoint(1, 1), enlarged)
    drawn = n * step
    if grid:
        p.setPen(QPen(GRID, 0))
        for i in range(1, n):
            p.drawLine(QPointF(1 + i * step, 1), QPointF(1 + i * step, 1 + drawn))
            p.drawLine(QPointF(1, 1 + i * step), QPointF(1 + drawn, 1 + i * step))
    middle = QRectF(1 + radius * step, 1 + radius * step, step, step)
    p.setPen(QPen(QColor("#000000"), 3))
    p.drawRect(middle.adjusted(-1, -1, 1, 1))
    p.setPen(QPen(QColor("#ffffff"), 1))
    p.drawRect(middle.adjusted(-1, -1, 1, 1))
    p.setPen(QPen(QColor(0, 0, 0, 160), 1))
    p.drawRect(QRectF(0.5, 0.5, whole + 1, whole + 1))
    if label:
        _paint_label(p, QRectF(0, whole + 2 + LABEL_GAP, whole + 2, LABEL_H), label, font, swatch)
    p.restore()


def _paint_label(p: QPainter, box: QRectF, label: str, font: QFont | None, swatch: QColor | None) -> None:
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(20, 20, 20, 235))
    p.drawRoundedRect(box, 6, 6)
    text_box = box
    if swatch is not None:
        p.setRenderHint(QPainter.Antialiasing, False)  # a crisp square
        p.setPen(QPen(QColor("#ffffff"), 1))
        p.setBrush(swatch)
        p.drawRect(QRectF(box.left() + 8.5, box.center().y() - 5.5, 11, 11))
        text_box = box.adjusted(24, 0, -4, 0)  # centred in what's left
    if font is not None:
        p.setFont(font)
    p.setPen(QColor("#ffffff"))
    p.drawText(text_box, Qt.AlignCenter, label)
