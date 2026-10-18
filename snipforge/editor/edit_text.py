"""Edit text in a screenshot: a line text recognition found is smart-erased, and the new words are
written over it in a matching size and colour, as an ordinary text box (so they stay editable).

The size is chosen so the new text's letters are as tall as the old ones were; the colours come
from the pixels: the background is what the line's edge mostly is, the ink what differs from it most.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage

from .style import FONT_RANGE, Style

ERASE_PAD = 3  # pixels around the line that are erased too (antialiasing, descenders OCR clipped)
TEXT_MARGIN = 8  # TextItem's document margin


@dataclass(frozen=True)
class Replacement:
    erase: QRectF  # where the old line is smart-erased
    style: Style  # the new text's look
    pos: QPointF  # where its text box goes, so its letters sit where the old ones did


def line_rect(line) -> QRectF:
    """The rectangle around a recognized line's words (snipforge.ocr.OcrLine)."""
    rect = QRectF()
    for word in line.words:
        rect = rect.united(word.rect) if not rect.isNull() else QRectF(word.rect)
    return rect


def _distance(a: QColor, b: QColor) -> int:
    return abs(a.red() - b.red()) + abs(a.green() - b.green()) + abs(a.blue() - b.blue())


def colors(image: QImage, rect: QRectF) -> tuple[QColor, QColor]:
    """(background, ink) under `rect`: the most common colour around its edge, and the average of
    the pixels inside that differ from it most."""
    area = rect.toAlignedRect().adjusted(-1, -1, 1, 1).intersected(image.rect())
    if area.isEmpty():
        return QColor("#ffffff"), QColor("#111111")
    step = max(1, max(area.width(), area.height()) // 200)  # a few hundred samples are plenty
    edge: dict[int, int] = {}
    for x in range(area.left(), area.right() + 1, step):
        for y in (area.top(), area.bottom()):
            rgb = image.pixel(x, y) & 0xFFFFFF
            edge[rgb] = edge.get(rgb, 0) + 1
    for y in range(area.top(), area.bottom() + 1, step):
        for x in (area.left(), area.right()):
            rgb = image.pixel(x, y) & 0xFFFFFF
            edge[rgb] = edge.get(rgb, 0) + 1
    paper = QColor(max(edge, key=edge.get))
    inside = [QColor(image.pixel(x, y)) for x in range(area.left(), area.right() + 1, step)
              for y in range(area.top(), area.bottom() + 1, step)]
    inside.sort(key=lambda c: _distance(c, paper), reverse=True)
    darkest = inside[:max(1, len(inside) // 10)]
    if not darkest or _distance(darkest[0], paper) < 60:  # no ink to speak of: dark on light, light on dark
        return paper, QColor("#111111" if paper.lightness() > 128 else "#ffffff")
    count = len(darkest)
    ink = QColor(round(sum(c.red() for c in darkest) / count), round(sum(c.green() for c in darkest) / count),
                 round(sum(c.blue() for c in darkest) / count))
    return paper, ink


def _font(size: int) -> QFont:
    from .text_items import _font as text_font
    return text_font(size, QFont.Normal)


def fitted_size(text: str, height: float) -> int:
    """The font size whose letters in `text` are about `height` tall (as recognition measured them)."""
    sample = text.strip() or "Ag"
    low, high = FONT_RANGE
    while low < high:
        middle = (low + high + 1) // 2
        if QFontMetricsF(_font(middle)).tightBoundingRect(sample).height() <= height:
            low = middle
        else:
            high = middle - 1
    return max(FONT_RANGE[0], low)


def replacement(image: QImage, rect: QRectF, old_text: str, new_text: str) -> Replacement:
    """How to replace the line `old_text` (at `rect` on `image`, the snip as shown) with `new_text`."""
    _paper, ink = colors(image, rect)
    size = fitted_size(old_text, rect.height())
    style = Style(color=ink.name(), fill=False, shadow=False, font_size=size, bold=False)
    metrics = QFontMetricsF(_font(size))
    ink_box = metrics.tightBoundingRect(old_text.strip() or "Ag")  # relative to the baseline
    # QGraphicsTextItem's first baseline sits at the margin plus the font's ascent
    pos = QPointF(rect.left() - TEXT_MARGIN - ink_box.left(),
                  rect.top() - TEXT_MARGIN - metrics.ascent() - ink_box.top())
    erase = QRectF(rect).adjusted(-ERASE_PAD, -ERASE_PAD, ERASE_PAD, ERASE_PAD)
    return Replacement(erase.intersected(QRectF(image.rect())), style, pos)

