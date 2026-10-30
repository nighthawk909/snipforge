"""How the canvas shows the snip: zoom, fit, and the dotted background around it."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPixmap

from .. import theme

DOT_GAP = 18  # on-screen pixels between background dots
MIN_ZOOM, MAX_ZOOM = 0.05, 16.0
SCENE_MARGIN = 60.0  # room to scroll past the snip's edges


class ViewMixin:
    """Zoom, fit and background for the Canvas (a QGraphicsView). Uses the canvas's `_mask`,
    `_crop_rect`, `_image_rect`, `_dot_tiles`, `frame_rect()` and `zoom_changed`."""

    def zoom(self) -> float:
        return self.transform().m11() or 1.0

    def fit_image(self) -> None:
        self.fit_rect(self._image_rect if self._mask.editing else self.frame_rect())

    def fit_rect(self, rect: QRectF) -> None:
        self.fitInView(rect.adjusted(-24, -24, 24, 24), Qt.KeepAspectRatio)
        if self.zoom() > 1.0:
            self.resetTransform()
        self._update_scene_rect()  # the room to move about is in scene units: it changes with the zoom
        self.centerOn(rect.center())
        self.zoom_changed.emit(self.zoom())

    def set_zoom(self, zoom: float) -> None:
        center = self.mapToScene(self.viewport().rect().center())
        self.resetTransform()
        self.scale(zoom, zoom)
        self._update_scene_rect()
        self.centerOn(center)
        self.zoom_changed.emit(self.zoom())

    def wheelEvent(self, e) -> None:
        if e.modifiers() & Qt.ControlModifier:
            factor = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
            new_zoom = self.zoom() * factor
            if MIN_ZOOM <= new_zoom <= MAX_ZOOM:
                self.scale(factor, factor)
                self._update_scene_rect()
                self.zoom_changed.emit(self.zoom())
        else:
            super().wheelEvent(e)

    def retheme(self, _theme=None) -> None:
        well = QColor(theme.current().palette.well)
        self.setBackgroundBrush(well)
        self._mask.set_background(well)
        self._dot_tiles.clear()
        self.viewport().update()

    def _dot_tile(self) -> QPixmap:
        """One grid cell with its dot, a whole number of physical pixels wide so it tiles without
        drifting at 125% or 175% scaling. Tiled by the painter, so a repaint is a single fill."""
        ratio = self.devicePixelRatioF() or 1.0
        tile = self._dot_tiles.get(ratio)
        if tile is not None:
            return tile
        t = theme.current().palette
        size = max(4, round(DOT_GAP * ratio))
        tile = QPixmap(size, size)
        tile.setDevicePixelRatio(ratio)
        tile.fill(QColor(t.well))
        gap = size / ratio
        p = QPainter(tile)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(t.dot))
        p.drawEllipse(QPointF(gap / 2, gap / 2), 1.1, 1.1)
        p.end()
        self._dot_tiles[ratio] = tile
        return tile

    def _background_pattern(self) -> tuple[QBrush, QPointF]:
        """The dotted background and where it starts, in viewport pixels (dots pan with the snip)."""
        tile = self._dot_tile()
        gap = tile.width() / tile.devicePixelRatio()
        origin = self.mapFromScene(QPointF(0, 0))
        return QBrush(tile), QPointF(origin.x() % gap - gap / 2, origin.y() % gap - gap / 2)

    def drawBackground(self, p: QPainter, rect: QRectF) -> None:
        brush, origin = self._background_pattern()
        p.save()
        p.resetTransform()
        p.setBrushOrigin(origin)
        p.fillRect(self.viewport().rect(), brush)
        p.restore()
        p.setPen(Qt.NoPen)
        for i, alpha in ((6, 30), (3, 50), (1, 90)):
            p.setBrush(QColor(0, 0, 0, alpha))
            p.drawRect(self._crop_rect.adjusted(-i, -i + 2, i, i + 2))
