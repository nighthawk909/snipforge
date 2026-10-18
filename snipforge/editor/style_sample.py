"""A small picture of what a style looks like on its tool: an arrow for arrows, a filled box for
filled rectangles. Used for your saved quick styles."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF

from .items import make_pen, readable_on
from .stamps import renderer
from .style import Style

MAX_PEN = 5.0  # thicker lines are drawn this thick, so the sample still fits its tile


def _pen(style: Style, scale: float = 0.6) -> QPen:
    return make_pen(QColor(style.color), min(MAX_PEN, max(1.0, style.width * scale)), style.dash)


def paint_sample(p: QPainter, rect: QRectF, kind: str, style: Style) -> None:
    """Draw `kind` in `style` inside `rect`."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    color = QColor(style.color)
    if kind in ("arrow", "line"):
        start, end = rect.bottomLeft() + QPointF(4, -4), rect.topRight() + QPointF(-4, 4)
        p.setPen(_pen(style))
        p.drawLine(start, end)
        if kind == "arrow":
            angle = math.atan2(end.y() - start.y(), end.x() - start.x())
            head = 7.0
            left = QPointF(end.x() - head * math.cos(angle - 0.5), end.y() - head * math.sin(angle - 0.5))
            right = QPointF(end.x() - head * math.cos(angle + 0.5), end.y() - head * math.sin(angle + 0.5))
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawPolygon(QPolygonF([end, left, right]))
    elif kind in ("rect", "ellipse"):
        box = rect.adjusted(4, 4, -4, -4)
        p.setPen(_pen(style))
        tint = QColor(color)
        tint.setAlpha(70)
        p.setBrush(tint if style.fill else Qt.NoBrush)
        if kind == "rect":
            p.drawRoundedRect(box, 2, 2)
        else:
            p.drawEllipse(box)
    elif kind in ("pen", "highlight"):
        path = QPainterPath(QPointF(rect.left() + 4, rect.center().y() + 4))
        path.cubicTo(QPointF(rect.left() + rect.width() * 0.35, rect.top()),
                     QPointF(rect.left() + rect.width() * 0.65, rect.bottom()),
                     QPointF(rect.right() - 4, rect.center().y() - 4))
        if kind == "highlight":
            color.setAlpha(170)
            p.setPen(QPen(color, min(10.0, style.width * 1.6 + 3), Qt.SolidLine, Qt.FlatCap, Qt.RoundJoin))
        else:
            p.setPen(_pen(style, 1.0))
        p.drawPath(path)
    elif kind == "step":
        d = min(rect.width(), rect.height()) - 6
        circle = QRectF(0, 0, d, d)
        circle.moveCenter(rect.center())
        p.setPen(QPen(QColor("#ffffff"), 1.5))
        p.setBrush(color)
        p.drawEllipse(circle)
        _text(p, circle, "1", readable_on(color), bold=True)
    elif kind == "stamp":
        side = min(rect.width(), rect.height()) - 6
        box = QRectF(0, 0, side, side)
        box.moveCenter(rect.center())
        renderer(style.stamp).render(p, box)
    elif kind in ("blur", "pixelate"):
        _paint_redaction(p, rect.adjusted(4, 6, -4, -6), kind, style)
    else:  # text, callout
        box = rect.adjusted(3, 5, -3, -5)
        filled = style.fill
        p.setPen(QPen(color, 1.5) if kind == "callout" or not filled else Qt.NoPen)
        p.setBrush(color if filled else QColor("#ffffff"))
        if kind == "callout" and style.shape == "ellipse":
            p.drawEllipse(box)
        elif kind == "callout" and style.shape == "rect":
            p.drawRect(box)
        else:
            radius = box.height() / 2 if style.shape == "pill" and kind == "callout" else 5
            p.drawRoundedRect(box, radius, radius)
        _text(p, box, "Aa", readable_on(color) if filled else (color if kind == "text" else QColor("#1c1c1e")),
              bold=True)
    p.restore()


def _text(p: QPainter, rect: QRectF, text: str, color: QColor, bold: bool = False) -> None:
    font = QFont(p.font())
    font.setPointSizeF(8)
    font.setBold(bold)
    p.setFont(font)
    p.setPen(color)
    p.drawText(rect, Qt.AlignCenter, text)


def _paint_redaction(p: QPainter, rect: QRectF, kind: str, style: Style) -> None:
    """Grey blocks, coarser for a stronger pixelation; soft bands for a blur."""
    p.setPen(Qt.NoPen)
    cells = max(2, round(9 - style.width / 2.5))
    w, h = rect.width() / cells, rect.height() / max(1, round(cells * rect.height() / rect.width()))
    y, row = rect.top(), 0
    while y < rect.bottom() - 0.5:
        x, col = rect.left(), 0
        while x < rect.right() - 0.5:
            shade = 150 + ((row * 37 + col * 53) % 5) * 18
            p.setBrush(QColor(shade, shade, shade, 255 if kind == "pixelate" else 160))
            p.drawRect(QRectF(x, y, min(w, rect.right() - x), min(h, rect.bottom() - y)))
            x, col = x + w, col + 1
        y, row = y + h, row + 1
