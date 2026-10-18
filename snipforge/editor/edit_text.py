"""Edit text in a screenshot: a line text recognition found is smart-erased, and the new words are
written over it in a matching size and colour, as an ordinary text box (so they stay editable).

Text recognition's box for a line is close to its letters but not always on them (small text can
come back a few pixels off), so the letters themselves are found in the pixels around it: they're
what's erased, what the new text's size is matched to, and where it's placed. The background is
what the line's edge mostly is; the ink, what differs from it most.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage

from .style import FONT_RANGE, Style

INK_DIFFERENCE = 90  # how far from the background a pixel must be to count as a letter (sum of RGB)
SEARCH = 0.6  # how far above and below the recognized line to look for its letters (times its height)
ERASE_PAD = 0.15  # erased around the letters, times their height (antialiasing), at least 2 px
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


def ink_box(image: QImage, rect: QRectF, paper: QColor) -> QRectF:
    """The letters of the line recognition put at `rect`: the rows next to it (and in it) that have
    ink on them, and the columns of those rows that do. `rect` itself when there's no ink to find."""
    reach = rect.height() * SEARCH
    window = rect.adjusted(-2, -reach, 2, reach).toAlignedRect().intersected(image.rect())
    if window.isEmpty():
        return QRectF(rect)
    rgb = paper.rgb() & 0xFFFFFF

    def inked(x: int, y: int) -> bool:
        pixel = image.pixel(x, y) & 0xFFFFFF
        return (abs((pixel >> 16) - (rgb >> 16)) + abs(((pixel >> 8) & 255) - ((rgb >> 8) & 255))
                + abs((pixel & 255) - (rgb & 255))) >= INK_DIFFERENCE

    columns = range(window.left(), window.right() + 1)
    rows = {y: any(inked(x, y) for x in columns) for y in range(window.top(), window.bottom() + 1)}
    middle = [y for y in rows if rect.top() <= y + 0.5 <= rect.bottom() and rows[y]]
    if not middle:
        return QRectF(rect)
    top, bottom = min(middle), max(middle)
    while top - 1 in rows and rows[top - 1]:  # letters run on above and below the box: follow them
        top -= 1
    while bottom + 1 in rows and rows[bottom + 1]:
        bottom += 1
    used = [x for x in columns if any(inked(x, y) for y in range(top, bottom + 1))]
    if not used:
        return QRectF(rect)
    return QRectF(min(used), top, max(used) - min(used) + 1, bottom - top + 1)


def _font(size: int) -> QFont:
    from .text_items import _font as text_font
    return text_font(size, QFont.Normal)


def fitted_size(text: str, width: float, height: float) -> int:
    """The font size at which `text` is as wide as the old line was (recognition measures widths
    well), unless its letters would then be far taller or shorter than the old ones: then the size
    whose letters are as tall."""
    sample = text.strip() or "Ag"

    def largest(fits) -> int:
        low, high = FONT_RANGE
        while low < high:
            middle = (low + high + 1) // 2
            if fits(QFontMetricsF(_font(middle)), middle):
                low = middle
            else:
                high = middle - 1
        return max(FONT_RANGE[0], low)

    by_width = largest(lambda m, _s: m.horizontalAdvance(sample) <= width)
    letters = QFontMetricsF(_font(by_width)).tightBoundingRect(sample).height()
    if 0.75 * height <= letters <= 1.35 * height:
        return by_width
    return largest(lambda m, _s: m.tightBoundingRect(sample).height() <= height)


def replacement(image: QImage, origin: QPointF, rect: QRectF, old_text: str, bounds: QRectF) -> Replacement:
    """How to replace the line recognized as `old_text` at `rect` (snip pixels). `image` is the snip
    as shown around it, its top-left at `origin`; nothing is erased outside `bounds` (the snip)."""
    local = rect.translated(-origin)
    paper, ink = colors(image, local)
    letters = ink_box(image, local, paper).translated(origin)
    size = fitted_size(old_text, letters.width(), letters.height())
    style = Style(color=ink.name(), fill=False, shadow=False, font_size=size, bold=False)
    metrics = QFontMetricsF(_font(size))
    sample = metrics.tightBoundingRect(old_text.strip() or "Ag")  # relative to the baseline
    # QGraphicsTextItem's first baseline sits at the margin plus the font's ascent
    pos = QPointF(letters.left() - TEXT_MARGIN - sample.left(),
                  letters.bottom() - TEXT_MARGIN - metrics.ascent() - sample.bottom())
    pad = max(2.0, letters.height() * ERASE_PAD)
    erase = letters.united(rect).adjusted(-pad, -pad, pad, pad)
    return Replacement(erase.intersected(bounds), style, pos)


def sample_area(rect: QRectF) -> QRectF:
    """The part of the snip around a line that replacement() needs to see."""
    reach = rect.height() * (SEARCH + 0.2) + 4
    return rect.adjusted(-6, -reach, 6, reach)
