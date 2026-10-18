"""Cut out: remove a strip of the snip and join what's left, with a straight or torn seam.

A cut is part of the snip's ImageGeometry (image_ops.py), so it's undoable, saved with the document,
and kept when the snip is turned, flipped or resized afterwards: each of those changes carries the
cuts along (turning a column cut makes it a row cut, and so on).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF

SEAMS = ("straight", "torn")
AXES = ("x", "y")  # x: remove columns (a vertical strip); y: remove rows (a horizontal strip)
TEAR = 6  # how far a torn seam's teeth reach either side of the join
TOOTH = 14.0  # average spacing of the teeth
MIN_LEFT = 8  # a cut always leaves at least this much of the snip


@dataclass(frozen=True)
class Cut:
    """Remove columns (axis "x") or rows (axis "y") start to end, and join the rest."""

    axis: str
    start: int
    end: int
    seam: str = "straight"

    @property
    def size(self) -> int:
        return self.end - self.start

    def to_list(self) -> list:
        return [self.axis, self.start, self.end, self.seam]

    @staticmethod
    def from_list(data, width: int, height: int) -> Cut | None:
        """None unless `data` is a cut that fits a `width` x `height` image."""
        if not isinstance(data, list) or len(data) != 4:
            return None
        axis, start, end, seam = data
        if axis not in AXES or seam not in SEAMS or type(start) is not int or type(end) is not int:
            return None
        cut = Cut(axis, start, end, seam)
        return cut if fits(cut, width, height) else None


def fits(cut: Cut, width: int, height: int) -> bool:
    length = width if cut.axis == "x" else height
    return 0 <= cut.start < cut.end <= length and length - cut.size >= MIN_LEFT


def size_after(width: int, height: int, cut: Cut) -> tuple[int, int]:
    return (width - cut.size, height) if cut.axis == "x" else (width, height - cut.size)


def frames(width: int, height: int, cuts: tuple[Cut, ...]) -> list[tuple[int, int]]:
    """The image's size before each cut."""
    sizes = []
    for cut in cuts:
        sizes.append((width, height))
        width, height = size_after(width, height, cut)
    return sizes


def final_size(width: int, height: int, cuts: tuple[Cut, ...]) -> tuple[int, int]:
    for cut in cuts:
        width, height = size_after(width, height, cut)
    return width, height


def map_coord(value: float, cut: Cut) -> float:
    """Where a coordinate along the cut's axis lands once the strip is gone (inside it: the seam)."""
    if value <= cut.start:
        return value
    if value >= cut.end:
        return value - cut.size
    return float(cut.start)


def map_point(point: QPointF, cut: Cut) -> QPointF:
    if cut.axis == "x":
        return QPointF(map_coord(point.x(), cut), point.y())
    return QPointF(point.x(), map_coord(point.y(), cut))


# ---------------------------------------------------------------------------
# Carrying cuts through a later turn, flip or resize (each in the frame it was made in)
# ---------------------------------------------------------------------------
def turned(cut: Cut, width: int, height: int, clockwise: bool) -> Cut:
    """The same cut on the image turned a quarter: clockwise (x, y) -> (h - y, x)."""
    if clockwise:
        return replace(cut, axis="y") if cut.axis == "x" else Cut("x", height - cut.end, height - cut.start, cut.seam)
    return Cut("y", width - cut.end, width - cut.start, cut.seam) if cut.axis == "x" else replace(cut, axis="x")


def flipped(cut: Cut, width: int, height: int, horizontal: bool) -> Cut:
    if horizontal and cut.axis == "x":
        return replace(cut, start=width - cut.end, end=width - cut.start)
    if not horizontal and cut.axis == "y":
        return replace(cut, start=height - cut.end, end=height - cut.start)
    return cut


def scaled_all(cuts: tuple[Cut, ...], width: int, height: int, sx: float, sy: float) -> tuple[Cut, ...]:
    """The cuts for the image resized by (sx, sy) to `width` x `height` (after its cuts).

    Worked back from the last cut, so each one fits the frame it's made in, and rounded outwards,
    so a cut only ever grows: resizing never brings back any of what was cut out."""
    out: list[Cut] = []
    w, h = width, height
    for cut in reversed(cuts):
        k = sx if cut.axis == "x" else sy
        start, end = math.floor(cut.start * k), math.ceil(cut.end * k)
        size = max(1, end - start)
        if cut.axis == "x":
            w += size
            start = min(start, w - size)
        else:
            h += size
            start = min(start, h - size)
        out.append(replace(cut, start=max(0, start), end=max(0, start) + size))
    return tuple(reversed(out))


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------
def _tear(cut: Cut, length: int) -> tuple[list[QPointF], list[QPointF]]:
    """The two torn edges of a torn seam, in the joined image: the first part's (just before the
    join) and the second part's (just after). The same cut always tears the same way."""
    rng = random.Random(f"{cut.axis}{cut.start}-{cut.end}-{length}")  # noqa: S311 - a repeatable look
    count = max(2, round(length / TOOTH))
    before, after = [], []
    for i in range(count + 1):
        along = length * i / count
        back, ahead = rng.uniform(1.5, TEAR), rng.uniform(1.5, TEAR)
        if cut.axis == "x":
            before.append(QPointF(cut.start - back, along))
            after.append(QPointF(cut.start + ahead, along))
        else:
            before.append(QPointF(along, cut.start - back))
            after.append(QPointF(along, cut.start + ahead))
    return before, after


def apply(image: QImage, cut: Cut) -> QImage:
    """`image` with the strip removed and the rest joined. A torn seam tears a few pixels off each
    side of the join, leaving a jagged white gap like torn paper: nothing of the removed strip ever
    shows."""
    w, h = size_after(image.width(), image.height(), cut)
    out = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    out.fill(Qt.transparent)
    p = QPainter(out)
    if cut.axis == "x":
        p.drawImage(0, 0, image, 0, 0, cut.start, h)
        p.drawImage(cut.start, 0, image, cut.end, 0, -1, -1)
        length = h
    else:
        p.drawImage(0, 0, image, 0, 0, w, cut.start)
        p.drawImage(0, cut.start, image, 0, cut.end, -1, -1)
        length = w
    if cut.seam == "torn":
        before, after = _tear(cut, length)
        gap = QPainterPath()
        gap.addPolygon(QPolygonF([*before, *reversed(after)]))
        gap.closeSubpath()
        p.setRenderHint(QPainter.Antialiasing)
        p.fillPath(gap, QColor("#ffffff"))
        p.setPen(QPen(QColor(0, 0, 0, 70), 1))
        for edge in (before, after):
            line = QPainterPath(edge[0])
            for point in edge[1:]:
                line.lineTo(point)
            p.drawPath(line)
    p.end()
    return out

