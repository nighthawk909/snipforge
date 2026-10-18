"""Hover highlight, selection outline, the drag-to-select rectangle and resize handles, drawn above
all annotations. Outlines are two-tone (a light band under the accent) so they show on any snip."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsItem, QProxyStyle, QStyle

from .. import theme

TAIL = QColor("#f59e0b")  # callout pointer handle: distinct from the accent on purpose
HANDLE_PX = 10  # on-screen size regardless of zoom
REACH_PX = HANDLE_PX / 2 + 4  # how far from a handle's middle a press still grabs it
HALO = QColor(255, 255, 255, 235)  # under every outline: the accent alone vanishes on dark snips

CURSORS = {
    "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
    "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
    "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
    "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
    "p1": Qt.SizeAllCursor, "p2": Qt.SizeAllCursor, "tail": Qt.SizeAllCursor,
    # a magnifier's source area
    "s_tl": Qt.SizeFDiagCursor, "s_br": Qt.SizeFDiagCursor, "s_tr": Qt.SizeBDiagCursor, "s_bl": Qt.SizeBDiagCursor,
    "source": Qt.SizeAllCursor,
}


class OwnRubberBand(QProxyStyle):
    """For the canvas's viewport: Qt's drag-to-select rectangle is a faint tint that disappears on dark
    snips, so the overlay draws it instead (`HandleOverlay.set_band`)."""

    def drawControl(self, element, option, painter, widget=None) -> None:  # noqa: N802 - Qt API
        if element != QStyle.CE_RubberBand:
            super().drawControl(element, option, painter, widget)


class HandleOverlay(QGraphicsItem):
    def __init__(self, bounds: QRectF) -> None:
        super().__init__()
        self._bounds = QRectF(bounds)
        self._selected: list = []
        self._hover = None
        self._band: QRectF | None = None  # the Select tool's drag-to-select rectangle
        self.setZValue(5000)
        self.setAcceptedMouseButtons(Qt.NoButton)

    def set_selection(self, items: list) -> None:
        self._selected = list(items)
        self.update()

    def set_hover(self, item) -> None:
        if item is not self._hover:
            self._hover = item
            self.update()

    def set_band(self, rect: QRectF | None) -> None:
        if rect != self._band:
            self._band = QRectF(rect) if rect is not None else None
            self.update()

    def set_bounds(self, bounds: QRectF) -> None:
        self.prepareGeometryChange()
        self._bounds = QRectF(bounds)

    @property
    def target(self):
        return self._selected[0] if len(self._selected) == 1 else None

    def boundingRect(self) -> QRectF:
        return self._bounds

    def handle_at(self, scene_pos: QPointF, scale: float) -> str | None:
        target = self.target
        if target is None or target.scene() is None:
            return None
        reach = REACH_PX / scale
        if hasattr(target, "scene_rect"):  # small markup: keep its middle for moving it
            box = target.scene_rect()
            reach = min(reach, max(HANDLE_PX / 2 / scale, min(box.width(), box.height()) / 3))
        best, best_distance = None, reach
        for name, pt in target.handle_points().items():  # the nearest wins where small markup's overlap
            distance = max(abs(pt.x() - scene_pos.x()), abs(pt.y() - scene_pos.y()))
            if distance <= best_distance:
                best, best_distance = name, distance
        return best

    def paint(self, p: QPainter, _opt, _widget=None) -> None:
        scale = p.worldTransform().m11() or 1.0
        accent = QColor(theme.current().accent.fill)
        hover = self._hover
        if hover is not None and hover.scene() is not None and hover not in self._selected:
            hover_color = QColor(accent)
            hover_color.setAlpha(200)
            for rect in _outline_rects(hover):
                _two_tone(p, rect, hover_color, scale, dashed=False, halo_alpha=150)
        tint = QColor(accent)
        tint.setAlpha(28)
        for item in self._selected:
            if item.scene() is None or len(item.handle_points()) == 2:  # lines show just their end handles
                continue
            for rect in _outline_rects(item):
                p.fillRect(rect, tint)
                _two_tone(p, rect, accent, scale, dashed=True)
        if self._band is not None:
            band_fill = QColor(accent)
            band_fill.setAlpha(45)
            p.fillRect(self._band, band_fill)
            _two_tone(p, self._band, accent, scale, dashed=False)
        target = self.target
        if target is None or target.scene() is None:
            return
        size = HANDLE_PX / scale
        for name, pt in target.handle_points().items():
            p.setPen(QPen(accent, 1.5 / scale))
            p.setBrush(TAIL if name == "tail" else QColor("#ffffff"))
            if name == "tail":
                p.drawEllipse(QRectF(pt.x() - size * 0.65, pt.y() - size * 0.65, size * 1.3, size * 1.3))
            else:
                p.drawRect(QRectF(pt.x() - size / 2, pt.y() - size / 2, size, size))


def _outline_rects(item) -> list[QRectF]:
    return item.selection_rects() if hasattr(item, "selection_rects") else [item.sceneBoundingRect()]


def _two_tone(p: QPainter, rect: QRectF, color: QColor, scale: float, dashed: bool, halo_alpha: int = 235) -> None:
    """A light band with the accent on top: one of the two stands out on any snip, dark or light."""
    halo = QColor(HALO)
    halo.setAlpha(halo_alpha)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(halo, 4.0 / scale))  # wide enough to show a whole light pixel beside the accent
    p.drawRect(rect)
    line = QPen(color, 1.5 / scale)
    if dashed:
        line.setDashPattern([4.0, 3.0])  # in units of the line's width
    p.setPen(line)
    p.drawRect(rect)
