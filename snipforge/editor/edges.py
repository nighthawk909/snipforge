"""Edge effects on the snip itself: a border, and torn edges on the sides you choose.

They belong to the snip (saved in its document, part of every export) and are drawn by an item
above all markup, so markup near a torn edge is torn too. Beautify (beautify.py) is separate and
unchanged: it frames whatever the snip looks like, edges included, with a backdrop that shows
through the torn parts.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field, replace

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem

SIDES = ("top", "right", "bottom", "left")
TOOTH = 14.0  # average width of one torn tooth
DEPTH = 9.0  # how far a tear reaches into the snip, at most
MAX_BORDER = 40


@dataclass(frozen=True)
class Edges:
    border: int = 0  # width in pixels; 0 is no border
    color: str = "#1c1c1e"
    torn: frozenset[str] = field(default_factory=frozenset)

    def is_plain(self) -> bool:
        return not self.border and not self.torn

    def to_dict(self) -> dict:
        return {"border": self.border, "color": self.color, "torn": sorted(self.torn, key=SIDES.index)}

    @staticmethod
    def from_dict(data) -> Edges:
        """Anything unusable reads as no effect."""
        if not isinstance(data, dict):
            return Edges()
        border = data.get("border", 0)
        border = border if type(border) is int and 0 <= border <= MAX_BORDER else 0
        color = data.get("color", "#1c1c1e")
        color = color if isinstance(color, str) and QColor(color).isValid() else "#1c1c1e"
        torn = data.get("torn", [])
        torn = frozenset(s for s in torn if s in SIDES) if isinstance(torn, list) else frozenset()
        return Edges(border, color, torn)

    def with_side(self, side: str, on: bool) -> Edges:
        return replace(self, torn=self.torn | {side} if on else self.torn - {side})


def _tear(start: QPointF, end: QPointF, inward: QPointF, rng: random.Random) -> list[QPointF]:
    """Points along a torn edge from `start` to `end`, each pushed up to DEPTH `inward`."""
    length = ((end.x() - start.x()) ** 2 + (end.y() - start.y()) ** 2) ** 0.5
    teeth = max(2, round(length / TOOTH))
    points = []
    for i in range(1, teeth):
        t = i / teeth + rng.uniform(-0.3, 0.3) / teeth
        depth = DEPTH * (0.25 + 0.75 * rng.random())
        points.append(QPointF(start.x() + (end.x() - start.x()) * t + inward.x() * depth,
                              start.y() + (end.y() - start.y()) * t + inward.y() * depth))
    return points


def outline(rect: QRectF, torn: frozenset[str]) -> QPainterPath:
    """The snip's outline: `rect`, with the `torn` sides replaced by jagged tears. The same rect
    always tears the same way, so the preview, exports and a reopened snip all match."""
    rng = random.Random(f"{rect.width():.0f}x{rect.height():.0f}")  # noqa: S311 - a repeatable look
    tl, tr, br, bl = rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()
    sides = (("top", tl, tr, QPointF(0, 1)), ("right", tr, br, QPointF(-1, 0)),
             ("bottom", br, bl, QPointF(0, -1)), ("left", bl, tl, QPointF(1, 0)))
    points = []
    for name, start, end, inward in sides:
        corner = QPointF(start)
        if name in torn:
            corner += inward * DEPTH * 0.6
        points.append(corner)
        if name in torn:
            points.extend(_tear(start, end, inward, rng))
    path = QPainterPath()
    path.addPolygon(QPolygonF(points))
    path.closeSubpath()
    return path


class EdgeItem(QGraphicsItem):
    """Draws the edges over the snip and its markup: the torn-away parts are cleared in exports and
    show what's behind the snip on screen; the border follows the outline, inside it."""

    def __init__(self, behind) -> None:
        """`behind(painter, path)` fills `path` with what's behind the snip on screen."""
        super().__init__()
        self._behind = behind
        self._rect = QRectF()
        self._edges = Edges()
        self.setZValue(990)  # above markup, below the crop mask and handles
        self.setAcceptedMouseButtons(Qt.NoButton)

    def set_edges(self, rect: QRectF, edges: Edges) -> None:
        self.prepareGeometryChange()
        self._rect, self._edges = QRectF(rect), edges
        self.update()

    def edges(self) -> Edges:
        return self._edges

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt API
        return self._rect.adjusted(-1, -1, 1, 1)

    def paint(self, p: QPainter, _opt, _widget=None) -> None:
        edges = self._edges
        if edges.is_plain() or self._rect.isEmpty():
            return
        shape = outline(self._rect, edges.torn)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        if edges.torn:
            frame = QPainterPath()
            frame.addRect(self._rect)
            gone = frame.subtracted(shape)
            if isinstance(p.device(), QImage):
                p.setCompositionMode(QPainter.CompositionMode_Clear)
                p.fillPath(gone, Qt.transparent)
                p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            else:
                self._behind(p, gone)
            if not edges.border:  # a faint line so the tear reads on a white snip
                p.setPen(QPen(QColor(0, 0, 0, 60), 1))
                p.setBrush(Qt.NoBrush)
                p.drawPath(shape)
        if edges.border:
            p.setClipPath(shape, Qt.IntersectClip)  # inside the outline, so the snip stays its size
            pen = QPen(QColor(edges.color), edges.border * 2)
            pen.setJoinStyle(Qt.MiterJoin)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(shape)
        p.restore()
