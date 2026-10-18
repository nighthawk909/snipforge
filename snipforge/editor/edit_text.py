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
MAX_INK = 0.65  # letters cover at most this much of their box (heavy bold text about half)
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


def _area(image: QImage, rect: QRectF):
    return rect.toAlignedRect().adjusted(-1, -1, 1, 1).intersected(image.rect())


def paper_color(image: QImage, rect: QRectF) -> QColor:
    """The background under `rect`: the most common colour around its edge."""
    area = _area(image, rect)
    if area.isEmpty():
        return QColor("#ffffff")
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
    return QColor(max(edge, key=edge.get))


def ink_color(image: QImage, rect: QRectF, paper: QColor) -> QColor:
    """The letters' colour under `rect`: the average of the pixels that differ most from `paper`
    (dark on light, light on dark when there's no ink to speak of)."""
    area = _area(image, rect)
    step = max(1, max(area.width(), area.height()) // 200)
    inside = [QColor(image.pixel(x, y)) for x in range(area.left(), area.right() + 1, step)
              for y in range(area.top(), area.bottom() + 1, step)]
    inside.sort(key=lambda c: _distance(c, paper), reverse=True)
    darkest = inside[:max(1, len(inside) // 10)]
    if not darkest or _distance(darkest[0], paper) < 60:
        return QColor("#111111" if paper.lightness() > 128 else "#ffffff")
    count = len(darkest)
    return QColor(round(sum(c.red() for c in darkest) / count), round(sum(c.green() for c in darkest) / count),
                  round(sum(c.blue() for c in darkest) / count))


def colors(image: QImage, rect: QRectF) -> tuple[QColor, QColor]:
    """(background, ink) under `rect`."""
    paper = paper_color(image, rect)
    return paper, ink_color(image, rect, paper)


def _runs(rows: dict[int, bool]) -> list[tuple[int, int]]:
    """Each stretch of inked rows, (first, last), top to bottom."""
    runs, start = [], None
    for y in sorted(rows):
        if rows[y] and start is None:
            start = y
        elif not rows[y] and start is not None:
            runs.append((start, y - 1))
            start = None
    if start is not None:
        runs.append((start, max(rows)))
    return runs


def _overlap(run: tuple[int, int], rect: QRectF) -> float:
    return max(0.0, min(run[1] + 1, rect.bottom()) - max(run[0], rect.top()))


def ink_box(image: QImage, rect: QRectF, paper: QColor) -> QRectF:
    """The letters of the line recognition put at `rect`. Of the stretches of inked rows that reach
    into it, the one most inside it whose letters start and end where it does (recognition's widths
    are good: that tells a line from the one above or below when the box sits between them), with
    any small stretch close by that's mostly inside it, like the dots on i's. `rect` itself when
    there are no such letters, or what's found doesn't look like one line (a busy background)."""
    reach = rect.height() * SEARCH
    window = rect.adjusted(-2, -reach, 2, reach).toAlignedRect().intersected(image.rect())
    if window.isEmpty() or rect.height() <= 0 or rect.width() <= 0:
        return QRectF(rect)
    rgb = paper.rgb() & 0xFFFFFF

    def inked(x: int, y: int) -> bool:
        pixel = image.pixel(x, y) & 0xFFFFFF
        return (abs((pixel >> 16) - (rgb >> 16)) + abs(((pixel >> 8) & 255) - ((rgb >> 8) & 255))
                + abs((pixel & 255) - (rgb & 255))) >= INK_DIFFERENCE

    columns = range(window.left(), window.right() + 1)

    def span(top: int, bottom: int) -> tuple[int, int] | None:
        used = [x for x in columns if any(inked(x, y) for y in range(top, bottom + 1))]
        return (min(used), max(used) + 1) if used else None

    def fit(run: tuple[int, int]) -> float:
        left, right = span(*run)
        misfit = (abs(left - rect.left()) + abs(right - rect.right())) / rect.width()
        return _overlap(run, rect) / (run[1] - run[0] + 1) - misfit

    runs = _runs({y: any(inked(x, y) for x in columns) for y in range(window.top(), window.bottom() + 1)})
    reaching = [run for run in runs if _overlap(run, rect) > 0]
    if not reaching:
        return QRectF(rect)
    best = max(reaching, key=fit)
    top, bottom = best
    gap = max(1.0, rect.height() * ERASE_PAD)
    for run in reaching:  # accents and dots just above or below, mostly inside the box
        near = run[0] - bottom - 1 <= gap if run[0] > bottom else top - run[1] - 1 <= gap
        if run != best and near and _overlap(run, rect) * 2 >= run[1] - run[0] + 1:
            top, bottom = min(top, run[0]), max(bottom, run[1])
    height = bottom - top + 1
    off = abs((top + bottom + 1) / 2 - rect.center().y())
    if not 0.4 * rect.height() <= height <= 1.6 * rect.height() or off > 0.75 * rect.height():
        return QRectF(rect)  # not one line of letters
    left, right = span(top, bottom)
    step = max(1, (right - left) // 300)
    samples = [inked(x, y) for x in range(left, right, step) for y in range(top, bottom + 1)]
    if sum(samples) > MAX_INK * len(samples):
        return QRectF(rect)  # solid colour, not letters: there's always paper between letters
    return QRectF(left, top, right - left, height)


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

    by_width = largest(lambda m, _s: m.tightBoundingRect(sample).width() <= width)  # letters to letters
    letters = QFontMetricsF(_font(by_width)).tightBoundingRect(sample).height()
    if 0.75 * height <= letters <= 1.35 * height:
        return by_width
    return largest(lambda m, _s: m.tightBoundingRect(sample).height() <= height)


def replacement(image: QImage, origin: QPointF, rect: QRectF, old_text: str, bounds: QRectF) -> Replacement:
    """How to replace the line recognized as `old_text` at `rect` (snip pixels). `image` is the snip
    as shown around it, its top-left at `origin`; nothing is erased outside `bounds` (the snip)."""
    local = rect.translated(-origin)
    paper = paper_color(image, local)
    found = ink_box(image, local, paper)
    ink = ink_color(image, found, paper)
    letters = found.translated(origin)
    size = fitted_size(old_text, letters.width(), letters.height())
    style = Style(color=ink.name(), fill=False, shadow=False, font_size=size, bold=False)
    metrics = QFontMetricsF(_font(size))
    sample = metrics.tightBoundingRect(old_text.strip() or "Ag")  # relative to the baseline
    # QGraphicsTextItem's first baseline sits at the margin plus the font's ascent
    pos = QPointF(letters.left() - TEXT_MARGIN - sample.left(),
                  letters.bottom() - TEXT_MARGIN - metrics.ascent() - sample.bottom())
    pad = max(2.0, letters.height() * ERASE_PAD)
    erase = letters.adjusted(-pad, -pad, pad, pad)  # the letters, not the box: it can reach the next line
    return Replacement(erase.intersected(bounds), style, pos)


def sample_area(rect: QRectF) -> QRectF:
    """The part of the snip around a line that replacement() needs to see."""
    reach = rect.height() * (SEARCH + 0.2) + 4
    return rect.adjusted(-6, -reach, 6, reach)
