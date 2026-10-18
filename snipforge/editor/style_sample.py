"""A small picture of what a style looks like on its tool: an arrow for arrows, a filled box for
filled rectangles. Used for your saved quick styles."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF

from .items import fill_ink, make_pen, readable_on
from .stamps import renderer
from .style import Style, fill_qcolor

MAX_PEN = 5.0  # thicker lines are drawn this thick, so the sample still fits its tile


def _pen(style: Style, scale: float = 0.6) -> QPen:
    return make_pen(QColor(style.color), min(MAX_PEN, max(1.0, style.width * scale)), style.dash, style.dotted)


def _lightness(color: QColor) -> float:
    return 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()


def _cased(p: QPainter, pen: QPen, background: QColor, draw) -> None:
    """Draw with `pen`, first with a contrasting casing if the color would vanish on `background`
    (white on a light tile, black on a dark one)."""
    if abs(_lightness(pen.color()) - _lightness(background)) < 60:
        casing = QPen(pen)
        casing.setColor(QColor("#1c1c1e") if _lightness(background) > 128 else QColor("#f2f2f7"))
        casing.setWidthF(pen.widthF() + 2)
        casing.setStyle(Qt.SolidLine)
        p.setPen(casing)
        draw()
    p.setPen(pen)
    draw()


def paint_sample(p: QPainter, rect: QRectF, kind: str, style: Style, background: QColor | None = None) -> None:
    """Draw `kind` in `style` inside `rect`, on a tile of color `background`."""
    background = background if background is not None else QColor("#ffffff")
    p.save()
    try:
        _paint(p, rect, kind, style, background)
    finally:
        p.restore()


def _paint(p: QPainter, rect: QRectF, kind: str, style: Style, background: QColor) -> None:
    p.setRenderHint(QPainter.Antialiasing)
    color = QColor(style.color)
    if kind in ("arrow", "line"):
        start, end = rect.bottomLeft() + QPointF(4, -4), rect.topRight() + QPointF(-4, 4)
        _cased(p, _pen(style), background, lambda: p.drawLine(start, end))
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
        p.setBrush(fill_qcolor(kind, style) if style.fill else Qt.NoBrush)
        shape = (lambda: p.drawRoundedRect(box, 2, 2)) if kind == "rect" else (lambda: p.drawEllipse(box))
        _cased(p, _pen(style), background, shape)
    elif kind in ("pen", "highlight"):
        path = QPainterPath(QPointF(rect.left() + 4, rect.center().y() + 4))
        path.cubicTo(QPointF(rect.left() + rect.width() * 0.35, rect.top()),
                     QPointF(rect.left() + rect.width() * 0.65, rect.bottom()),
                     QPointF(rect.right() - 4, rect.center().y() - 4))
        if kind == "highlight":
            color.setAlpha(170)
            pen = QPen(color, min(10.0, style.width * 1.6 + 3), Qt.SolidLine, Qt.FlatCap, Qt.RoundJoin)
        else:
            pen = _pen(style, 1.0)
        _cased(p, pen, background, lambda: p.drawPath(path))
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
    elif kind == "magnify":
        source = QRectF(rect.left() + 3, rect.bottom() - 11, 8, 8)
        lens = QRectF(rect.left() + 14, rect.top() + 2, rect.height() - 4, rect.height() - 4)
        p.setPen(QPen(color, 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(source)
        p.setPen(_pen(style))
        p.setBrush(QColor("#ffffff"))
        if style.shape == "ellipse":
            p.drawEllipse(lens)
        else:
            p.drawRoundedRect(lens, 4 if style.shape != "rect" else 0, 4 if style.shape != "rect" else 0)
    elif kind in ("blur", "pixelate"):
        _paint_redaction(p, rect.adjusted(4, 6, -4, -6), kind, style)
    elif kind == "text" and not style.fill:  # just the letters, as unfilled text has no box
        if abs(_lightness(color) - _lightness(background)) < 60:
            backing = QColor("#1c1c1e") if _lightness(color) > 128 else QColor("#ffffff")
            p.setPen(Qt.NoPen)
            p.setBrush(backing)
            p.drawRoundedRect(rect.adjusted(6, 5, -6, -5), 5, 5)
        _text(p, rect, "Aa", color, bold=True)
    else:  # filled text, callout
        box = rect.adjusted(3, 5, -3, -5)
        filled = style.fill
        p.setPen(QPen(color, 1.5) if kind == "callout" or not filled else Qt.NoPen)
        p.setBrush(fill_qcolor(kind, style) if filled else QColor("#ffffff"))
        if kind == "callout" and style.shape == "ellipse":
            p.drawEllipse(box)
        elif kind == "callout" and style.shape == "rect":
            p.drawRect(box)
        else:
            radius = box.height() / 2 if style.shape == "pill" and kind == "callout" else 5
            p.drawRoundedRect(box, radius, radius)
        _text(p, box, "Aa", _sample_ink(kind, style), bold=True)
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
    cells = min(9, max(2, round(9 - style.width / 2.5)))
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


def _sample_ink(kind: str, style: Style) -> QColor:
    """The "Aa" color on a text or callout sample, as the real item colors its text."""
    if not style.fill:
        return QColor(style.color) if kind == "text" else QColor("#1c1c1e")
    return fill_ink(kind, style)
