"""The eyedropper on the canvas: pick any pixel's color, markup included, as the current color."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QCursor

from .canvas_input import NUDGE_KEYS
from .eyedropper import sample

PICK_KEYS = NUDGE_KEYS  # the arrow keys


class PickMixin:
    """For the Canvas. While picking, mouse moves show the loupe and a click picks; without a mouse,
    the arrow keys move the pixel the loupe shows (Shift: 10 at a time) and Enter picks it. Esc, a
    right-click, choosing a tool, picking, leaving the window or any change to the snip (undo, a
    turn...) ends it. Picks come from a picture of the snip taken when picking starts, so moving
    the pointer stays cheap; ending on every change keeps that picture true."""

    def pick_point(self) -> QPointF | None:
        """The middle of the pixel the loupe shows, or None before the pointer or a key moved it."""
        return QPointF(self._pick_point) if self._pick_point is not None else None

    def picking(self) -> bool:
        return self._pick_source is not None

    def start_picking(self) -> None:
        self.stop_choosing()
        self.commit_text()
        self._cancel_drag()
        if self.tool == "crop":
            self.set_tool("select")  # leaving Crop applies it, so you pick from what you'll export
        # Without markup or edges the snip itself is the picture (shared, not copied).
        drawn_on = self.annotations() or not self._edges.is_plain()
        source = self._render_rect(self._image_rect.toAlignedRect()) if drawn_on else self.base_image
        if source.isNull():
            return  # out of memory for the picture: no eyedropper rather than wrong colors
        self._pick_source = source
        self._pick_point = None
        self._overlay.set_hover(None)
        self.viewport().setCursor(Qt.CrossCursor)
        self.picking_changed.emit(True)

    def stop_picking(self) -> None:
        if self._pick_source is None:
            return
        self._pick_source = None
        self._pick_point = None
        self._pick_by_key = False
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
        """Scrolling or zooming moves the snip under a still pointer (or the keys' point under the
        loupe, which stays on the same pixel and in view)."""
        if not self.picking():
            return
        if self._pick_by_key and self._pick_point is not None:
            if not self._refreshing:
                self._refreshing = True  # ensureVisible scrolls, which calls this again
                try:
                    self._show_key_point(self._pick_point)
                finally:
                    self._refreshing = False
            return
        view_pos = self.viewport().mapFromGlobal(QCursor.pos())
        if self.viewport().rect().contains(view_pos):
            self._pick_hover(view_pos, self.mapToScene(view_pos))
        else:
            self._loupe.hide()

    def _pick_hover(self, view_pos, scene_pos: QPointF) -> None:
        x, y = math.floor(scene_pos.x()), math.floor(scene_pos.y())
        self._pick_point = QPointF(x + 0.5, y + 0.5)
        color = self.color_at(scene_pos)
        self._loupe.show_at(view_pos, sample(self._pick_source, x, y), color.name().upper() if color else "")

    def _pick_key(self, e) -> bool:
        """Arrow keys move the pixel the loupe shows, Enter picks it. True if the key was used."""
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            if e.isAutoRepeat():
                return True  # a held Enter picks once
            if self._pick_point is None:
                self._pick_move(0, 0)  # show where Enter would pick, first
            else:
                self._pick_press(self._pick_point)
            return True
        if e.key() not in PICK_KEYS:
            return False
        step = 10 if e.modifiers() & Qt.ShiftModifier else 1
        dx, dy = PICK_KEYS[e.key()]
        self._pick_move(dx * step, dy * step)
        return True

    def _pick_move(self, dx: int, dy: int) -> None:
        """Move the keyboard's pixel, starting from the middle of the snip you can see, never off it."""
        crop = self._crop_rect
        if self._pick_point is None:
            visible = self.mapToScene(self.viewport().rect()).boundingRect().intersected(crop)
            middle = (visible if not visible.isEmpty() else crop).center()
            self._pick_point = QPointF(math.floor(middle.x()) + 0.5, math.floor(middle.y()) + 0.5)
        x = min(max(self._pick_point.x() + dx, crop.left() + 0.5), crop.right() - 0.5)
        y = min(max(self._pick_point.y() + dy, crop.top() + 0.5), crop.bottom() - 0.5)
        self._pick_by_key = True
        self._show_key_point(QPointF(x, y))

    def _show_key_point(self, point: QPointF) -> None:
        self.ensureVisible(QRectF(point.x() - 0.5, point.y() - 0.5, 1, 1), 40, 40)
        self._pick_hover(self.mapFromScene(point), point)

    def _pick_press(self, scene_pos: QPointF) -> None:
        color = self.color_at(scene_pos)
        if color is None:
            return  # outside the snip: keep picking
        self.stop_picking()
        self._just_picked = True  # the second click of a double-click mustn't edit text under it
        self.apply_style(color=color.name())
        self.color_picked.emit(color.name())
