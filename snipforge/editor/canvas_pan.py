"""Moving the snip around the window by dragging: the middle mouse button with any tool, or the
Select tool on the empty area around the snip (Space with any tool is QGraphicsView's own hand
drag). There's room to move it whether or not it fits the window, never so far that it's lost, and
a double-click on the empty area brings it back."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt

from .canvas_view import SCENE_MARGIN

PAN_ROOM = 0.8  # how far past the snip's edges it can be moved, as a share of the window


class PanMixin:
    """For the Canvas. Uses its `tool`, `_panning` (Space held), `_mask`, `_image_rect`, `frame_rect()`,
    `_annotation_at()`, `_overlay`, `_area_handle_at()`, `editing_text_item()`, `picking()`,
    `choosing()` and `zoom()`."""

    _pan_from: QPoint | None = None
    _pan_button = Qt.NoButton

    def pan_room(self) -> tuple[float, float]:
        """Scene units of room around the snip, so it can move about however it fits the window."""
        zoom, viewport = self.zoom(), self.viewport()
        return (max(SCENE_MARGIN, viewport.width() * PAN_ROOM / zoom),
                max(SCENE_MARGIN, viewport.height() * PAN_ROOM / zoom))

    def on_empty_area(self, view_pos: QPoint) -> bool:
        """Whether this spot in the window is the empty area around the snip, with no markup or handle."""
        pos = self.mapToScene(view_pos)
        frame = self._image_rect if self._mask.editing else self.frame_rect()
        if frame.contains(pos) or self._annotation_at(view_pos) is not None:
            return False
        return not (self._overlay.handle_at(pos, self.zoom()) or self._area_handle_at(view_pos, pos))

    def _begin_pan(self, e) -> bool:
        """Start moving the snip if this press does that: True if it did."""
        middle = e.button() == Qt.MiddleButton
        select_on_empty = (e.button() == Qt.LeftButton and self.tool == "select" and not self._panning
                           and self._free_to_pan() and self.on_empty_area(e.position().toPoint()))
        if not (middle or select_on_empty):
            return False
        self._pan_from, self._pan_button = e.position().toPoint(), e.button()
        self.viewport().setCursor(Qt.ClosedHandCursor)
        return True

    def _free_to_pan(self) -> bool:
        """Not typing, picking a color or choosing a line of text, where a click means something else."""
        return self.editing_text_item() is None and not self.picking() and not self.choosing()

    def _continue_pan(self, e) -> bool:
        """Move with the pointer while a pan is under way: True if one is."""
        if self._pan_from is None:
            return False
        if not e.buttons() & self._pan_button:  # its release went missing (another window took it)
            self._stop_pan()
            return False
        pos = e.position().toPoint()
        delta, self._pan_from = pos - self._pan_from, pos
        h, v = self.horizontalScrollBar(), self.verticalScrollBar()
        h.setValue(h.value() - delta.x())
        v.setValue(v.value() - delta.y())
        return True

    def _end_pan(self, e) -> bool:
        if self._pan_from is None or e.button() != self._pan_button:
            return False
        self._stop_pan()
        return True

    def _stop_pan(self) -> None:
        self._pan_from = None
        self.viewport().setCursor(Qt.ArrowCursor if self.tool == "select" else Qt.CrossCursor)

    def resizeEvent(self, e) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(e)
        self._update_scene_rect()  # the room to move about follows the window's size
