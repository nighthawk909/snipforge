"""Choosing a line of text on the canvas (Edit text): the lines text recognition found are offered,
the one under the pointer is outlined, and a click picks it. Esc, a right-click or choosing a tool
ends it. Nothing is drawn, moved or selected meanwhile."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt

PICK_SLOP = 4.0  # a click this close to a line (in snip pixels) still picks it


class ChooseMixin:
    """For the Canvas, which calls _choose_press, _choose_hover and stop_choosing from its input."""

    _choices: list[QRectF] | None = None
    _on_chosen = None

    def choosing(self) -> bool:
        return self._choices is not None

    def start_choosing(self, rects: list[QRectF], on_chosen) -> None:
        """Offer `rects` (snip pixels); `on_chosen(index)` is called with the one clicked."""
        self.stop_picking()
        self.commit_text()
        self._cancel_drag()
        if self.tool == "crop":
            self.set_tool("select")  # leaving Crop applies it, so the lines are on what you keep
        self._choices = [QRectF(r) for r in rects]
        self._on_chosen = on_chosen
        self._scene.clearSelection()
        self.viewport().setCursor(Qt.PointingHandCursor)
        self.choosing_changed.emit(True)

    def stop_choosing(self) -> None:
        if self._choices is None:
            return
        self._choices = None
        self._on_chosen = None
        self._overlay.set_band(None)
        self.viewport().setCursor(Qt.ArrowCursor if self.tool == "select" else Qt.CrossCursor)
        self.choosing_changed.emit(False)

    def choice_at(self, pos: QPointF) -> int | None:
        """The line under `pos`, or the nearest within PICK_SLOP of it."""
        if not self._choices:
            return None
        best, best_distance = None, PICK_SLOP
        for n, rect in enumerate(self._choices):
            dx = max(rect.left() - pos.x(), 0.0, pos.x() - rect.right())
            dy = max(rect.top() - pos.y(), 0.0, pos.y() - rect.bottom())
            distance = max(dx, dy)
            if distance <= best_distance:
                best, best_distance = n, distance
        return best

    def _choose_hover(self, pos: QPointF) -> None:
        index = self.choice_at(pos)
        self._overlay.set_band(self._choices[index].adjusted(-2, -2, 2, 2) if index is not None else None)

    def _choose_press(self, pos: QPointF) -> None:
        index = self.choice_at(pos)
        if index is None:
            return  # a click beside every line: keep choosing
        on_chosen = self._on_chosen
        self.stop_choosing()
        on_chosen(index)
