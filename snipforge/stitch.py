"""Scrolling capture's stitching: frames of a scrolling window become one tall image.

Each new frame is compared with the one before, row by row (a fingerprint per row): how far did
the content move? Only the rows that scrolled into view are added. Rows that stay put at the top
and bottom (a sticky header or footer) are found from the first scroll and appear once, at the top
and bottom of the result. Columns that never change (a folder list beside a message list) are left
out of the comparison and of the result, and so is the scrollbar, because its thumb moves.
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
STATIC_PANEL = 16  # pixels: a still side panel at least this wide is left out of the result


def row_signatures(image: QImage, left_margin: int = 0, right_margin: int = 0) -> list[int]:
    """A fingerprint per row, ignoring `left_margin` and `right_margin` columns (the scrollbar)."""
    image = image.convertToFormat(QImage.Format_RGB32)
    width, per_line = image.width(), image.bytesPerLine()
    start, end = left_margin * 4, max(left_margin + 1, width - right_margin) * 4
    bits = image.constBits()
    return [hash(bytes(bits[y * per_line + start:y * per_line + end])) for y in range(image.height())]


def _first_difference(a: bytes, b: bytes) -> int:
    """Index of the first byte where two equal-length byte strings differ (they must differ)."""
    lo, hi = 0, len(a)
    while lo < hi:  # a[:lo] == b[:lo] always holds
        mid = (lo + hi) // 2
        if a[lo:mid + 1] == b[lo:mid + 1]:
            lo = mid + 1
        else:
            hi = mid
    return lo


def changed_columns(a: QImage, b: QImage, step: int = 4) -> tuple[int, int] | None:
    """The leftmost and rightmost columns that differ between two same-size frames (looking at every
    `step`th row), or None if they look the same."""
    a, b = a.convertToFormat(QImage.Format_RGB32), b.convertToFormat(QImage.Format_RGB32)
    width, per_line = a.width(), a.bytesPerLine()
    a_bits, b_bits = a.constBits(), b.constBits()
    left, right = width, -1
    for y in range(0, a.height(), step):
        row_a = bytes(a_bits[y * per_line:y * per_line + width * 4])
        row_b = bytes(b_bits[y * per_line:y * per_line + width * 4])
        if row_a == row_b:
            continue
        left = min(left, _first_difference(row_a, row_b) // 4)
        right = max(right, width - 1 - _first_difference(row_a[::-1], row_b[::-1]) // 4)
    return None if right < 0 else (left, right)


def find_shift(prev: list[int], cur: list[int], header: int = 0, footer: int = 0) -> int | None:
    """How many rows the content moved up between two frames (their row signatures), comparing only
    the rows between `header` and `footer`. None if it didn't move or the frames don't line up."""
    prev_band, cur_band = prev[header:len(prev) - footer], cur[header:len(cur) - footer]
    size = min(len(prev_band), len(cur_band))
    min_overlap = max(MIN_OVERLAP, size // 10)
    if size <= min_overlap or prev_band == cur_band:
        return None  # too small to tell, or nothing moved

    def score(shift: int) -> float:
        overlap = size - shift
        return sum(cur_band[i] == prev_band[i + shift] for i in range(overlap)) / overlap

    still = score(0)  # how well "it didn't move" explains the new frame
    best, best_score = None, MIN_MATCH
    for shift in _candidates(prev_band, cur_band, size - min_overlap):
        value = score(shift)
        if value > still and (value > best_score or (value == best_score and best is not None and shift < best)):
            best, best_score = shift, value
    return best


def _anchors(band: list[int]) -> list[tuple[int, tuple[int, int]]]:
    """(row, key) where a row differs from the one above it, keyed by the pair: the edge of a line of
    text or a block. Pairs are far more distinctive than single rows on pages of solid blocks and
    blank space."""
    return [(i, (band[i - 1], band[i])) for i in range(1, len(band)) if band[i] != band[i - 1]]


def _candidates(prev_band: list[int], cur_band: list[int], max_shift: int) -> list[int]:
    """Likely shifts: where edges that appear once in the old frame turn up in the new one."""
    prev_edges = _anchors(prev_band)
    counts = Counter(key for _, key in prev_edges)
    where = {key: i for i, key in prev_edges if counts[key] == 1}
    votes = Counter(where[key] - i for i, key in _anchors(cur_band) if key in where)
    likely = [shift for shift, _ in votes.most_common(5) if 0 < shift <= max_shift]
    if likely or votes:
        return likely
    return list(range(1, max_shift + 1))  # nothing distinctive (a plain page): try every shift


class Stitcher:
    """Feed frames in scroll order with add(); result() is the stitched image."""

    def __init__(self, max_height: int = DEFAULT_MAX_HEIGHT, right_margin: int | None = None) -> None:
        self.max_height = max_height
        self._right_margin = right_margin
        self._first: QImage | None = None
        self._prev: list[int] | None = None
        self._prev_frame: QImage | None = None
        self._columns: tuple[int, int] | None = None  # the columns that scroll; known after the first scroll
        self._size = None
        self._header = self._footer = None  # rows; known after the first scroll
        self._body: list[QImage] = []
        self._footer_image: QImage | None = None
        self._height = 0
        self.full = False  # stopped at max_height
        self.last_shift = 0  # rows the content moved in the last frame that added any
        # What the last add() found: "added", "still" (nothing moved: the end, or not redrawn yet),
        # "mismatch" (doesn't line up: e.g. grabbed mid-redraw) or "full" (the height limit).
        self.outcome = ""

    def _signatures(self, frame: QImage, columns: tuple[int, int] | None = None) -> list[int]:
        margin = self._right_margin
        if margin is None:  # leave out where a scrollbar would be
            margin = max(0, min(24, frame.width() // 8))
        if columns is None:
            return row_signatures(frame, right_margin=margin)
        left, right = columns
        return row_signatures(frame, left_margin=left, right_margin=max(margin, frame.width() - 1 - right))

    def add(self, frame: QImage) -> bool:
        """Add the next frame. False when nothing new came into view: the end of the page, a frame
        that doesn't line up with the last one, or the height limit."""
        frame = frame.convertToFormat(QImage.Format_RGB32)
        if self._first is None:
            self._first, self._prev_frame, self._size = frame, frame, frame.size()
            self._prev = self._signatures(frame)
            self._height = frame.height()
            self.outcome = "added"
            return True
        if frame.size() != self._size:
            raise ValueError("every frame of a scrolling capture must be the same size")
        if self.full:
            self.outcome = "full"
            return False
        rows = frame.height()
        columns, prev = self._columns, self._prev
        if columns is None:  # until the first scroll: work out which columns move
            columns = changed_columns(self._prev_frame, frame)
            if columns is None:
                self.outcome = "still"
                return False
            prev = self._signatures(self._prev_frame, columns)
        signatures = self._signatures(frame, columns)
        header, footer = self._header, self._footer
        if header is None:
            header, footer = self._still_rows(prev, signatures)
            if header + footer >= rows:
                self.outcome = "still"
                return False
        shift = find_shift(prev, signatures, header, footer)
        if shift is None:
            band = slice(header, rows - footer)
            self.outcome = "still" if prev[band] == signatures[band] else "mismatch"
            return False
        if self._header is None:
            self._columns = columns
            self._settle_edges(header, footer)
        room = self.max_height - self._height
        if room <= 0:
            self.full = True
            self.outcome = "full"
            return False
        take = min(shift, room)
        bottom = rows - footer
        self._body.append(frame.copy(QRect(0, bottom - shift, frame.width(), take)))
        self._footer_image = frame.copy(QRect(0, bottom, frame.width(), footer)) if footer else None
        self._height += take
        self.last_shift = shift
        self._prev, self._prev_frame = signatures, frame
        if take < shift:
            self.full = True
            self.outcome = "full"
            return False
        self.outcome = "added"
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

    def _kept_columns(self) -> tuple[int, int]:
        """The columns the result keeps: all of them, less a still side panel (left or right). A still
        strip that's one flat color is the page's own margin, and stays."""
        width = self._first.width()
        if self._columns is None:
            return 0, width
        left, right = self._columns[0], self._columns[1] + 1
        keep_left = left if left >= STATIC_PANEL and not self._flat(0, left) else 0
        keep_right = right if width - right >= STATIC_PANEL and not self._flat(right, width) else width
        return keep_left, keep_right

    def _flat(self, x0: int, x1: int) -> bool:
        strip = self._first.copy(QRect(x0, 0, x1 - x0, self._first.height()))
        first = strip.pixel(0, 0)
        return all(strip.pixel(x, y) == first for y in range(0, strip.height(), 3) for x in range(strip.width()))

    def result(self) -> QImage:
        if self._first is None:
            raise ValueError("no frames yet")
        if self._header is None:
            return self._first.copy()
        parts = [self._first.copy(QRect(0, 0, self._first.width(), self._header))] if self._header else []
        parts += self._body
        if self._footer_image is not None:
            parts.append(self._footer_image)
        left, right = self._kept_columns()
        parts = [part.copy(QRect(left, 0, right - left, part.height())) for part in parts]
        out = QImage(right - left, sum(p.height() for p in parts), QImage.Format_RGB32)
        painter = QPainter(out)
        y = 0
        for part in parts:
            painter.drawImage(0, y, part)
            y += part.height()
        painter.end()
        return out
