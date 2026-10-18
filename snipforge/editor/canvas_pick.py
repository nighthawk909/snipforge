"""The eyedropper on the canvas: pick any pixel's color, markup included, as the current color."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QCursor

from .eyedropper import sample


class PickMixin:
    """For the Canvas. While picking, mouse moves show the loupe and a click picks. Esc, a
    right-click, choosing a tool, picking, leaving the window or any change to the snip (undo, a
    turn...) ends it. Picks come from a picture of the snip taken when picking starts, so moving
    the pointer stays cheap; ending on every change keeps that picture true."""

    def picking(self) -> bool:
        return self._pick_source is not None

    def start_picking(self) -> None:
        self.commit_text()
        self._cancel_drag()
        if self.tool == "crop":
            self.set_tool("select")  # leaving Crop applies it, so you pick from what you'll export
        # Without markup the snip itself is the picture (shared, not copied).
        source = self._render_rect(self._image_rect.toAlignedRect()) if self.annotations() else self.base_image
        if source.isNull():
            return  # out of memory for the picture: no eyedropper rather than wrong colors
        self._pick_source = source
        self._overlay.set_hover(None)
        self.viewport().setCursor(Qt.CrossCursor)
        self.picking_changed.emit(True)

    def stop_picking(self) -> None:
        if self._pick_source is None:
            return
        self._pick_source = None
        self._loupe.hide()
        self.viewport().setCursor(Qt.ArrowCursor if self.tool == "select" else Qt.CrossCursor)
        self.picking_changed.emit(False)

    def color_at(self, scene_pos: QPointF) -> QColor | None:
        """The color you'd pick at `scene_pos`: None outside the snip (or the part you cropped)."""
        if self._pick_source is None:
            return None
        x, y = math.floor(scene_pos.x()), math.floor(scene_pos.y())
        if not self._crop_rect.contains(QPointF(x + 0.5, y + 0.5)) or not self._pick_source.valid(x, y):
            return None
        color = self._pick_source.pixelColor(x, y)
        return QColor(color.red(), color.green(), color.blue()) if color.alpha() else None

    def _pick_scene_pos(self, e) -> QPointF:
        """Where the pointer is, unrounded: at 125% or 150% Windows scaling it can sit mid-pixel,
        and rounding would pick the pixel beside the one under it."""
        inverse, _ok = self.viewportTransform().inverted()
        return inverse.map(e.position())

    def _refresh_loupe(self, *_args) -> None:
        """Scrolling or zooming moves the snip under a still pointer."""
        if not self.picking():
            return
        view_pos = self.viewport().mapFromGlobal(QCursor.pos())
        if self.viewport().rect().contains(view_pos):
            self._pick_hover(view_pos, self.mapToScene(view_pos))
        else:
            self._loupe.hide()

    def _pick_hover(self, view_pos, scene_pos: QPointF) -> None:
        color = self.color_at(scene_pos)
        pixels = sample(self._pick_source, math.floor(scene_pos.x()), math.floor(scene_pos.y()))
        self._loupe.show_at(view_pos, pixels, color.name().upper() if color is not None else "")

    def _pick_press(self, scene_pos: QPointF) -> None:
        color = self.color_at(scene_pos)
        if color is None:
            return  # outside the snip: keep picking
        self.stop_picking()
        self._just_picked = True  # the second click of a double-click mustn't edit text under it
        self.apply_style(color=color.name())
        self.color_picked.emit(color.name())
