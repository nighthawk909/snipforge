"""Scrolling capture's stitching: frames of a scrolling window become one tall image.

Each new frame is compared with the one before, row by row (a fingerprint per row): how far did
the content move? Only the rows that scrolled into view are added. Rows that stay put at the top
and bottom (a sticky header or footer) are found from the first scroll and appear once, at the top
and bottom of the result. The scrollbar is left out of the comparison, because its thumb moves.
Everything here is plain image work, the same on every platform.
"""
from __future__ import annotations

from collections import Counter

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage, QPainter

MIN_MATCH = 0.8  # share of overlapping rows that must agree for a shift to count
MIN_OVERLAP = 8  # rows; less than this and a "match" could be chance
MAX_HEADER = 0.4  # share of the frame a sticky header may take
MAX_FOOTER = 0.3
DEFAULT_MAX_HEIGHT = 30_000  # pixels: past this a page is endless (or an infinite feed)


def row_signatures(image: QImage, left_margin: int = 0, right_margin: int = 0) -> list[int]:
    """A fingerprint per row, ignoring `left_margin` and `right_margin` columns (the scrollbar)."""
    image = image.convertToFormat(QImage.Format_RGB32)
    width, per_line = image.width(), image.bytesPerLine()
    start, end = left_margin * 4, max(left_margin + 1, width - right_margin) * 4
    bits = image.constBits()
    return [hash(bytes(bits[y * per_line + start:y * per_line + end])) for y in range(image.height())]


def find_shift(prev: list[int], cur: list[int], header: int = 0, footer: int = 0) -> int | None:
    """How many rows the content moved up between two frames (their row signatures), comparing only
    the rows between `header` and `footer`. None if it didn't move or the frames don't line up."""
    prev_band, cur_band = prev[header:len(prev) - footer], cur[header:len(cur) - footer]
    size = min(len(prev_band), len(cur_band))
    min_overlap = max(MIN_OVERLAP, size // 10)
    if size <= min_overlap:
        return None
    candidates = _candidates(prev_band, cur_band, size - min_overlap)
    best, best_score = None, MIN_MATCH
    for shift in candidates:
        overlap = size - shift
        score = sum(cur_band[i] == prev_band[i + shift] for i in range(overlap)) / overlap
        if score > best_score or (score == best_score and best is not None and shift < best):
            best, best_score = shift, score
    return best


def _candidates(prev_band: list[int], cur_band: list[int], max_shift: int) -> list[int]:
    """Likely shifts: where rows that appear once in the old frame turn up in the new one."""
    counts = Counter(prev_band)
    where = {sig: i for i, sig in enumerate(prev_band) if counts[sig] == 1}
    votes = Counter(where[sig] - i for i, sig in enumerate(cur_band) if sig in where)
    likely = [shift for shift, _ in votes.most_common(5) if 0 < shift <= max_shift]
    if likely or votes:
        return likely
    return list(range(1, max_shift + 1))  # no distinctive rows (a plain page): try every shift


class Stitcher:
    """Feed frames in scroll order with add(); result() is the stitched image."""

    def __init__(self, max_height: int = DEFAULT_MAX_HEIGHT, right_margin: int | None = None) -> None:
        self.max_height = max_height
        self._right_margin = right_margin
        self._first: QImage | None = None
        self._prev: list[int] | None = None
        self._size = None
        self._header = self._footer = None  # rows; known after the first scroll
        self._body: list[QImage] = []
        self._footer_image: QImage | None = None
        self._height = 0
        self.full = False  # stopped at max_height

    def _signatures(self, frame: QImage) -> list[int]:
        margin = self._right_margin
        if margin is None:  # leave out where a scrollbar would be
            margin = max(0, min(24, frame.width() // 8))
        return row_signatures(frame, right_margin=margin)

    def add(self, frame: QImage) -> bool:
        """Add the next frame. False when nothing new came into view: the end of the page, a frame
        that doesn't line up with the last one, or the height limit."""
        frame = frame.convertToFormat(QImage.Format_RGB32)
        signatures = self._signatures(frame)
        if self._first is None:
            self._first, self._prev, self._size = frame, signatures, frame.size()
            self._height = frame.height()
            return True
        if frame.size() != self._size:
            raise ValueError("every frame of a scrolling capture must be the same size")
        if self.full:
            return False
        rows = frame.height()
        header, footer = self._header, self._footer
        if header is None:
            header, footer = self._still_rows(self._prev, signatures)
            if header + footer >= rows:
                return False  # nothing moved
        shift = find_shift(self._prev, signatures, header, footer)
        if shift is None:
            return False
        if self._header is None:
            self._settle_edges(header, footer)
        room = self.max_height - self._height
        if room <= 0:
            self.full = True
            return False
        take = min(shift, room)
        bottom = rows - footer
        self._body.append(frame.copy(QRect(0, bottom - shift, frame.width(), take)))
        self._footer_image = frame.copy(QRect(0, bottom, frame.width(), footer)) if footer else None
        self._height += take
        self._prev = signatures
        if take < shift:
            self.full = True
            return False
        return True

    def _still_rows(self, prev: list[int], cur: list[int]) -> tuple[int, int]:
        """Rows that didn't change at the top and bottom between two frames: sticky header and footer."""
        rows = len(cur)
        header = next((i for i in range(rows) if prev[i] != cur[i]), rows)
        footer = next((i for i in range(rows) if prev[rows - 1 - i] != cur[rows - 1 - i]), rows)
        if header >= rows:
            return rows, 0
        return min(header, int(rows * MAX_HEADER)), min(footer, int(rows * MAX_FOOTER))

    def _settle_edges(self, header: int, footer: int) -> None:
        """The first scroll shows what's sticky: split the first frame into header, body and footer."""
        self._header, self._footer = header, footer
        first = self._first
        rows = first.height()
        self._body = [first.copy(QRect(0, header, first.width(), rows - header - footer))]
        self._footer_image = first.copy(QRect(0, rows - footer, first.width(), footer)) if footer else None

    def result(self) -> QImage:
        if self._first is None:
            raise ValueError("no frames yet")
        if self._header is None:
            return self._first.copy()
        parts = [self._first.copy(QRect(0, 0, self._first.width(), self._header))] if self._header else []
        parts += self._body
        if self._footer_image is not None:
            parts.append(self._footer_image)
        out = QImage(self._first.width(), sum(p.height() for p in parts), QImage.Format_RGB32)
        painter = QPainter(out)
        y = 0
        for part in parts:
            painter.drawImage(0, y, part)
            y += part.height()
        painter.end()
        return out
