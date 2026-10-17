"""Scrolling capture's stitching: frames of a scrolling window become one tall image.

Each new frame is compared with the one before. Row fingerprints, matched through the edges
between rows (distinctive even on pages of blank space and solid blocks), show how far the content
moved, and only the rows that scrolled into view are added. Every comparison looks again at what
stayed put: rows at the top and bottom (a header that docks, a banner that slides in) and columns
at the sides (a folder list), so they're neither matched against nor repeated down the result. A
shift that many distinctive edges agree on is trusted even when part of the view changed in place
(a highlight, an animation); without them a much closer match is needed, and a tie goes to the
scroll distance the caller expects (periodic content like log lines). A scrollbar at the right
edge is recognised by its moving thumb and left out of the result. Everything here is plain image
work, the same on every platform.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage, QPainter

MIN_MATCH = 0.8  # agreement needed when nothing distinctive vouches for a shift
ANCHORED_MATCH = 0.5  # with matching edges vouching for it, half the overlap may have changed in place
MIN_ANCHORS = 3  # distinctive edges that must agree before a shift counts as vouched for
MIN_OVERLAP = 8  # rows; less than this and a "match" could be chance
MAX_HEADER = 0.4  # share of the frame a sticky header may take
MAX_FOOTER = 0.3
DEFAULT_MAX_HEIGHT = 30_000  # pixels: past this a page is endless (or an infinite feed)
DEFAULT_MAX_PIXELS = 60_000_000  # about 240 MB of image: a 4K-wide page stops near 15,600 px
STATIC_PANEL = 16  # pixels: a still side panel at least this wide is left out of the result
SCROLLBAR_MAX = 32  # pixels (at 100% scaling) from the right edge where a scrollbar is looked for
SCROLLBAR_STILL = 0.7  # a scrollbar column mostly stays put (its track) while its thumb moves
SCROLLBAR_BORDER = 3  # pixels (at 100%) of unchanging border allowed between a scrollbar and the edge
STILL_CHUNKS = 8  # a row is split into this many pieces to tell "mostly the same" (a counter in a status bar)
STILL_SHARE = 0.75  # of the pieces that must match for a row to count as staying put
END_MATCH = 0.9  # a view this close to the last one, unshifted, is the end of the page (not a mismatch)


def _rows_bits(image: QImage):
    return image.constBits(), image.bytesPerLine()


def row_signatures(image: QImage, left_margin: int = 0, right_margin: int = 0) -> list[int]:
    """A fingerprint per row, ignoring `left_margin` and `right_margin` columns."""
    image = image.convertToFormat(QImage.Format_RGB32)
    width = image.width()
    bits, per_line = _rows_bits(image)
    start, end = left_margin * 4, max(left_margin + 1, width - right_margin) * 4
    return [hash(bytes(bits[y * per_line + start:y * per_line + end])) for y in range(image.height())]


def uniform_rows(image: QImage, rows: Iterable[int], left: int = 0, right: int | None = None) -> list[bool]:
    """Whether each of `rows` is one flat color between columns `left` and `right` (exclusive)."""
    image = image.convertToFormat(QImage.Format_RGB32)
    right = image.width() if right is None else right
    bits, per_line = _rows_bits(image)
    flags = []
    for y in rows:
        row = bytes(bits[y * per_line + left * 4:y * per_line + right * 4])
        flags.append(row == row[:4] * (len(row) // 4))
    return flags


def _mostly_same(a: QImage, b: QImage, y: int, left: int, right: int) -> bool:
    """Whether row `y` of two frames matches in most of its pieces (between columns left and right)."""
    a_bits, per_line = _rows_bits(a)
    b_bits, _ = _rows_bits(b)
    width = max(1, right - left)
    step = max(1, width // STILL_CHUNKS)
    same = total = 0
    for x in range(left, right, step):
        end = min(right, x + step)
        total += 1
        start_byte, end_byte = y * per_line + x * 4, y * per_line + end * 4
        same += a_bits[start_byte:end_byte] == b_bits[start_byte:end_byte]
    return total > 0 and same / total >= STILL_SHARE


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
    width = a.width()
    a_bits, per_line = _rows_bits(a)
    b_bits, _ = _rows_bits(b)
    left, right = width, -1
    for y in range(0, a.height(), step):
        row_a = bytes(a_bits[y * per_line:y * per_line + width * 4])
        row_b = bytes(b_bits[y * per_line:y * per_line + width * 4])
        if row_a == row_b:
            continue
        left = min(left, _first_difference(row_a, row_b) // 4)
        right = max(right, width - 1 - _first_difference(row_a[::-1], row_b[::-1]) // 4)
    return None if right < 0 else (left, right)


def find_shift(prev: list[int], cur: list[int], header: int = 0, footer: int = 0,
               expected: int | None = None, blank: frozenset[int] = frozenset()) -> int | None:
    """How many rows the content moved up between two frames (their row signatures), comparing only
    the rows between `header` and `footer`. None if it didn't move or the frames don't line up.
    `expected` (the distance the scroll should have gone) breaks ties on repetitive content."""
    prev_band, cur_band = prev[header:len(prev) - footer], cur[header:len(cur) - footer]
    size = min(len(prev_band), len(cur_band))
    min_overlap = max(MIN_OVERLAP, size // 10)
    max_shift = size - min_overlap
    if size <= min_overlap or prev_band == cur_band:
        return None  # too small to tell, or nothing moved

    def score(shift: int) -> float:
        overlap = size - shift
        return sum(cur_band[i] == prev_band[i + shift] for i in range(overlap)) / overlap

    rows = min(len(prev), len(cur))

    def zone(k: int) -> int:
        return 0 if k < header else (2 if k >= rows - footer else 1)

    def crossing(shift: int) -> int:
        """Rows that agree across the band's edges at this shift: a block that only looked still
        (it sits at the bottom of both views) lines up with page rows. A real sticky bar only ever
        agrees with itself, so it never counts, however small the shift."""
        return sum(cur[i] == prev[i + shift] and cur[i] not in blank  # blank rows match anything: no evidence
                   for i in range(max(0, rows - shift)) if zone(i) != zone(i + shift))

    def nearer(a: int, b: int | None) -> bool:
        """For equally good shifts: the one more rows across the band's edges agree with, then the
        one more distinctive edges point to, then the expected distance, then the smaller."""
        if b is None:
            return True
        if (header or footer) and crossing(a) != crossing(b):
            return crossing(a) > crossing(b)
        if votes[a] != votes[b]:
            return votes[a] > votes[b]
        if expected is not None and abs(a - expected) != abs(b - expected):
            return abs(a - expected) < abs(b - expected)
        return a < b

    still = score(0)  # how well "it didn't move" explains the new frame
    votes = _votes(prev_band, cur_band, max_shift)
    best, best_score = None, 0.0
    for shift, count in votes.most_common(5):  # shifts many distinctive edges agree on
        if count < MIN_ANCHORS:
            break
        value = score(shift)
        if value >= ANCHORED_MATCH and value > still and (value > best_score or
                                                         (value == best_score and nearer(shift, best))):
            best, best_score = shift, value
    if best is not None:
        return best
    best_score = MIN_MATCH  # nothing vouched for: try every shift, and want a close match
    for shift in range(1, max_shift + 1):
        value = score(shift)
        if value > still and (value > best_score or (value == best_score and nearer(shift, best))):
            best, best_score = shift, value
    return best


def _votes(prev_band: list[int], cur_band: list[int], max_shift: int) -> Counter:
    """Shifts suggested by edges (a row unlike the one above it) that appear once in the old frame."""
    def edges(band):
        return [(i, (band[i - 1], band[i])) for i in range(1, len(band)) if band[i] != band[i - 1]]
    prev_edges = edges(prev_band)
    counts = Counter(key for _, key in prev_edges)
    where = {key: i for i, key in prev_edges if counts[key] == 1}
    return Counter(shift for i, key in edges(cur_band)
                   if key in where and 0 < (shift := where[key] - i) <= max_shift)


class Stitcher:
    """Feed frames in scroll order with add(); result() is the stitched image."""

    def __init__(self, max_height: int = DEFAULT_MAX_HEIGHT, max_pixels: int = DEFAULT_MAX_PIXELS,
                 scale: float = 1.0) -> None:
        """`scale`: the monitor's scaling (devicePixelRatio), for sizes like a scrollbar's width."""
        self.max_height = max_height
        self.max_pixels = max_pixels
        self._scale = max(1.0, scale)
        self._scrollbar_seen: int | None = None  # a scrollbar width seen once, waiting to be seen again
        self._first: QImage | None = None
        self._prev: QImage | None = None
        self._size = None
        self._header: int | None = None  # rows of the first frame kept as the header; set by the first scroll
        self._body_rows = (0, 0)  # the first frame's body rows (for telling panels from margins)
        self._strips: list[QImage] = []  # body rows, in order
        self._footer: QImage | None = None  # bottom rows that stayed put in the last frame
        self._last_bottom = 0  # in the last frame: where the rows added so far end
        self._height = 0
        self._changed: tuple[int, int] | None = None  # columns that changed in any scroll
        self._scrollbar = 0  # columns at the right edge that are a scrollbar (left out of the result)
        self.expected: int | None = None  # how far the next scroll should go, if the caller knows
        self.full = False  # stopped at the size limit
        self.last_shift = 0  # rows the content moved in the last frame that added any
        self.header_rows = self.footer_rows = 0  # what stayed put in the last scroll (the band between scrolls)
        # What the last add() found: "added", "still" (nothing moved: the end, or not redrawn yet),
        # "mismatch" (doesn't line up: e.g. grabbed mid-redraw) or "full" (the size limit).
        self.outcome = ""

    def add(self, frame: QImage) -> bool:
        """Add the next frame. False when nothing new came into view (see `outcome` for why)."""
        frame = frame.convertToFormat(QImage.Format_RGB32)
        if self._first is None:
            self._first, self._prev, self._size = frame, frame, frame.size()
            self._height = self._last_bottom = frame.height()
            self.outcome = "added"
            return True
        if frame.size() != self._size:
            raise ValueError("every frame of a scrolling capture must be the same size")
        if self.full:
            self.outcome = "full"
            return False
        rows, width = frame.height(), frame.width()
        changed = changed_columns(self._prev, frame)
        if changed is None:
            self.outcome = "still"
            return False
        left, right = changed
        zone = self._px(SCROLLBAR_MAX)
        scrollbar_zone = width - (self._scrollbar or zone) - 1
        if left >= scrollbar_zone and self.expected and self._header is not None:
            # Only the scrollbar's thumb moved: the page scrolled through a blank stretch with
            # nothing to line up. Trust the wheel's distance.
            return self._add_rows(frame, self.expected, self.header_rows, self.footer_rows, changed)
        if right - left >= 2 * zone:  # compare without a scrollbar's moving thumb
            right = max(left, min(right, width - 1 - min(self._px(24), width // 8)))
        prev_sig = row_signatures(self._prev, left, width - 1 - right)
        cur_sig = row_signatures(frame, left, width - 1 - right)
        header, footer = self._still_rows(prev_sig, cur_sig, self._prev, frame, left, right + 1)
        if header + footer >= rows:
            self.outcome = "still"
            return False
        flat_rows = Counter(sig for frame_, sigs in ((self._prev, prev_sig), (frame, cur_sig))
                            for sig, flat in zip(sigs, uniform_rows(frame_, range(rows), left, right + 1), strict=True)
                            if flat)
        # The page's background (its most common flat row) matches anywhere: no evidence of a shift.
        # A colored block is flat too, but it's somewhere in particular.
        blank = frozenset(sig for sig, _ in flat_rows.most_common(1))
        shift = find_shift(prev_sig, cur_sig, header, footer, self.expected, blank)
        if shift is None:
            band = range(header, rows - footer)
            unshifted = sum(prev_sig[i] == cur_sig[i] for i in band) / max(1, len(band))
            self.outcome = "still" if unshifted >= END_MATCH else "mismatch"
            return False
        return self._add_rows(frame, shift, header, footer, changed)

    def _px(self, size: int) -> int:
        return round(size * self._scale)

    def _add_rows(self, frame: QImage, shift: int, header: int, footer: int, changed: tuple[int, int]) -> bool:
        """The page moved up `shift` rows between the last frame and this one: add what came into view."""
        rows, width = frame.height(), frame.width()
        if self._header is None:  # the first scroll: what's on top stays the header
            self._header = header
            self._body_rows = (header, rows - footer)
            self._strips = [self._first.copy(QRect(0, header, width, rows - footer - header))]
            self._last_bottom = rows - footer
            self._height = rows - footer
        elif self._last_bottom > rows - footer:  # something slid in at the bottom and stayed: the rows
            self._retract(self._last_bottom - (rows - footer))  # added from there last time weren't page
            self._last_bottom = rows - footer
        # Right where the added rows ended, even inside what now looks like a header: rows held back
        # as a "footer" last time (often blank page) are owed, and skipping them would lose page.
        start, end = max(0, self._last_bottom - shift), rows - footer
        room = min(self.max_height, self.max_pixels // width) - self._height - footer
        take = min(end - start, room)
        if take <= 0:
            self.full = True
            self.outcome = "full"
            return False
        self._strips.append(frame.copy(QRect(0, start, width, take)))
        self._footer = frame.copy(QRect(0, end, width, footer)) if footer else None
        self._height += take
        self._last_bottom = start + take
        self._changed = changed if self._changed is None else (min(self._changed[0], changed[0]),
                                                                max(self._changed[1], changed[1]))
        if not self._scrollbar:
            self._settle_scrollbar(self._find_scrollbar(self._prev, frame, header, rows - footer))
        self.last_shift, self.header_rows, self.footer_rows = shift, header, footer
        self._prev = frame
        if take < end - start:
            self.full = True
            self.outcome = "full"
            return False
        self.outcome = "added"
        return True

    def _retract(self, rows: int) -> None:
        """Take the last `rows` rows back off the body (they turned out to be a footer)."""
        while rows > 0 and self._strips:
            last = self._strips[-1]
            if last.height() <= rows and len(self._strips) > 1:
                self._strips.pop()
                rows -= last.height()
                self._height -= last.height()
            else:  # the first strip keeps at least a row
                trim = min(rows, last.height() - 1)
                self._strips[-1] = last.copy(QRect(0, 0, last.width(), last.height() - trim))
                self._height -= trim
                rows = 0

    @staticmethod
    def _still_rows(prev: list[int], cur: list[int], prev_frame: QImage, frame: QImage,
                    left: int, right: int) -> tuple[int, int]:
        """Rows at the top and bottom that stayed put between two frames: a sticky header or footer,
        even one with something changing inside it (a counter, a progress bar). Blank page that
        happens to line up may count too; rows held back as a footer are added on a later scroll,
        since the stitcher tracks where the added rows end."""
        rows = len(cur)

        def still(y: int) -> bool:
            return prev[y] == cur[y] or _mostly_same(prev_frame, frame, y, left, right)

        if all(prev[y] == cur[y] for y in range(rows)):
            return rows, 0
        header = next((i for i in range(int(rows * MAX_HEADER) + 1) if not still(i)), int(rows * MAX_HEADER))
        footer = next((i for i in range(int(rows * MAX_FOOTER) + 1) if not still(rows - 1 - i)), int(rows * MAX_FOOTER))
        return header, footer

    def _settle_scrollbar(self, width: int) -> None:
        """Trust a scrollbar only once two scrolls agree on its width."""
        if not width:
            return
        if self._scrollbar_seen is not None and abs(width - self._scrollbar_seen) <= self._px(1):
            self._scrollbar = min(width, self._scrollbar_seen)
        self._scrollbar_seen = width

    def _find_scrollbar(self, prev: QImage, cur: QImage, top: int, bottom: int) -> int:
        """Width of a scrollbar at the right edge, 0 if there's none. In a scrollbar column only the
        thumb moved: the changes are at most two solid runs (where it was, where it is), and the rest
        is the track, one color. Text near a blank margin changes in many short runs instead."""
        width = cur.width()
        zone = min(self._px(SCROLLBAR_MAX), width // 4)
        prev_px = memoryview(prev.constBits()).cast("I")
        cur_px = memoryview(cur.constBits()).cast("I")
        per_row = prev.bytesPerLine() // 4
        found = width
        for x in range(width - 1, width - 1 - zone, -1):
            column = range(top, bottom)
            changed = [prev_px[y * per_row + x] != cur_px[y * per_row + x] for y in column]
            if not changed:
                break
            if not any(changed):
                if found == width and width - 1 - x >= self._px(SCROLLBAR_BORDER):
                    return 0  # nothing moving right at the edge: no scrollbar there
                continue  # a thin border at the edge, or between parts of the scrollbar
            runs = sum(1 for i, c in enumerate(changed) if c and (i == 0 or not changed[i - 1]))
            track = Counter(cur_px[y * per_row + x] for y, c in zip(column, changed, strict=True) if not c)
            still_share = 1 - sum(changed) / len(changed)
            if runs <= 2 and still_share >= SCROLLBAR_STILL and track and \
                    sum(n for _, n in track.most_common(2)) >= 0.95 * sum(track.values()) and \
                    Stitcher._thumb_moved(prev_px, cur_px, per_row, x, column, changed, track.most_common(1)[0][0]):
                found = x
            else:
                break  # content that scrolls: the scrollbar (if any) ends here
        if found < width and found - 2 >= 0:
            # A page element (a full-width bar) moving past looks like a thumb too, but it carries on
            # to the left of where the scrollbar would be; a real thumb stops at the scrollbar.
            x = found - 2
            beside = [prev_px[y * per_row + x] != cur_px[y * per_row + x] for y in range(top, bottom)]
            edge = [prev_px[y * per_row + found] != cur_px[y * per_row + found] for y in range(top, bottom)]
            if beside == edge:
                return 0
        if found < width:  # the track's own edge beside the thumb: plain track color, never changes
            column = range(top, bottom)
            track = Counter(cur_px[y * per_row + found] for y in column).most_common(1)[0][0]
            for _ in range(self._px(SCROLLBAR_BORDER)):
                x = found - 1
                if x < 0 or any(cur_px[y * per_row + x] != track or prev_px[y * per_row + x] != track
                                for y in column):
                    break
                found = x
        return width - found

    @staticmethod
    def _thumb_moved(prev_px, cur_px, per_row: int, x: int, column, changed, track_color: int) -> bool:
        """A scrollbar's thumb is in both views, in the same one color, just somewhere else: where it
        was it's track now, where it is it was track before. Page content scrolling past isn't."""
        rows = [y for y, c in zip(column, changed, strict=True) if c]
        before = Counter(prev_px[y * per_row + x] for y in rows if prev_px[y * per_row + x] != track_color)
        after = Counter(cur_px[y * per_row + x] for y in rows if cur_px[y * per_row + x] != track_color)
        if not before or not after:
            return False
        # The thumb's own color (its edges may be blended) is the same before and after, and it's about
        # the same size: content scrolling past is neither.
        same_color = before.most_common(1)[0][0] == after.most_common(1)[0][0]
        same_size = 0.5 <= sum(after.values()) / sum(before.values()) <= 2
        return same_color and same_size

    def _kept_columns(self) -> tuple[int, int]:
        """The columns the result keeps: all of them, less the scrollbar and a still side panel. A
        still strip that's one flat color in the body is the page's own margin, and stays."""
        width = self._first.width()
        right_edge = width - self._scrollbar
        if self._changed is None:
            return 0, right_edge
        top, bottom = self._body_rows
        left, right = self._changed[0], min(self._changed[1] + 1, right_edge)

        def flat(x0, x1):  # one color throughout, like a page margin
            strip = self._first.copy(QRect(x0, top, x1 - x0, bottom - top))
            if strip.isNull():
                return True
            return all(uniform_rows(strip, range(strip.height()))) and \
                len({strip.pixel(0, y) for y in range(strip.height())}) == 1
        keep_left = left if left >= STATIC_PANEL and not flat(0, left) else 0
        keep_right = right if right_edge - right >= STATIC_PANEL and not flat(right, right_edge) else right_edge
        return keep_left, keep_right

    def result(self) -> QImage:
        if self._first is None:
            raise ValueError("no frames yet")
        if self._header is None:
            return self._first.copy()
        left, right = self._kept_columns()
        parts = [self._first.copy(QRect(0, 0, self._first.width(), self._header))] if self._header else []
        parts += self._strips
        if self._footer is not None:
            parts.append(self._footer)
        out = QImage(right - left, sum(p.height() for p in parts), QImage.Format_RGB32)
        painter = QPainter(out)
        y = 0
        for part in parts:  # drawn straight from each strip: no extra copies of a tall page
            painter.drawImage(0, y, part, left, 0, right - left, part.height())
            y += part.height()
        painter.end()
        return out
