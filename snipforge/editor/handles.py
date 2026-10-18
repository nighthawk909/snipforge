"""Hover highlight, selection outline and resize handles, drawn above all annotations."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsItem

from .. import theme

TAIL = QColor("#f59e0b")  # callout pointer handle: distinct from the accent on purpose
HANDLE_PX = 9  # on-screen size regardless of zoom

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


class HandleOverlay(QGraphicsItem):
    def __init__(self, bounds: QRectF) -> None:
        super().__init__()
        self._bounds = QRectF(bounds)
        self._selected: list = []
        self._hover = None
        self.setZValue(5000)
        self.setAcceptedMouseButtons(Qt.NoButton)

    def set_selection(self, items: list) -> None:
        self._selected = list(items)
        self.update()

    def set_hover(self, item) -> None:
        if item is not self._hover:
            self._hover = item
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
        reach = (HANDLE_PX / 2 + 3) / scale
        for name, pt in target.handle_points().items():
            if abs(pt.x() - scene_pos.x()) <= reach and abs(pt.y() - scene_pos.y()) <= reach:
                return name
        return None

    def paint(self, p: QPainter, _opt, _widget=None) -> None:
        scale = p.worldTransform().m11() or 1.0
        accent = QColor(theme.current().accent.fill)
        hover_color = QColor(accent)
        hover_color.setAlpha(150)
        hover = self._hover
        if hover is not None and hover.scene() is not None and hover not in self._selected:
            p.setPen(QPen(hover_color, 2.0 / scale))
            p.setBrush(Qt.NoBrush)
            p.drawRect(hover.sceneBoundingRect())
        outline = QPen(accent, 1.0 / scale, Qt.DashLine)
        for item in self._selected:
            if item.scene() is None:
                continue
            if len(item.handle_points()) != 2:  # lines show just their end handles
                p.setPen(outline)
                p.setBrush(Qt.NoBrush)
                rects = item.selection_rects() if hasattr(item, "selection_rects") else [item.sceneBoundingRect()]
                for rect in rects:
                    p.drawRect(rect)
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
