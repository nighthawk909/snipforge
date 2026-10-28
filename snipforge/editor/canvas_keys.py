"""Markup from the keyboard, for people who don't use a mouse:

- Tab and Shift+Tab select the markup one at a time, in the order it's drawn; past the last (or
  before the first) the keyboard moves on to the next part of the window, so it's never trapped.
- Enter with a drawing tool adds its markup in the middle of the part of the snip you can see,
  selected (the arrow keys then move it). Freehand tools need a pointer, and say so.
- Ctrl+arrow keys make what's selected wider, narrower, taller or shorter (with Shift, by 10).
- While cropping, the arrow keys move the crop and Ctrl+arrow keys size it (Enter keeps it).
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtWidgets import QGraphicsItem

from .commands import StateCommand
from .crop import MIN_CROP

POINTER_TOOLS = {"pen": "the pen", "highlight": "the highlighter", "move": "Smart Move", "cutout": "Cut out"}
PLACED_SHARE = 0.25  # markup placed from the keyboard: this much of the visible snip's width and height
PLACED_MIN = (40.0, 30.0)


class KeyboardMixin:
    """For the Canvas: uses its scene, selection, tool, undo stack, `notice` and DrawMixin's creation."""

    def focusNextPrevChild(self, forward: bool) -> bool:  # noqa: N802 - Qt API
        """Tab and Shift+Tab on the canvas (Qt asks this only when no markup is being typed in:
        the scene keeps Tab then). Not an event() override, which would run Python for every event."""
        if self.hasFocus() and not (self.choosing() or self.picking() or self.crop_editing()):
            if self.step_selection(forward):
                return True
            self._scene.clearSelection()  # past the end: on to the next part of the window
        return super().focusNextPrevChild(forward)

    def step_selection(self, forward: bool) -> bool:
        """Select the next (or previous) markup; False past the end, or when there's none."""
        items = [i for i in self.bottom_up(self.annotations()) if not i.hidden
                 if i.isVisible() and i.flags() & QGraphicsItem.ItemIsSelectable]
        if not items:
            return False
        selected = self.selected_annotations()
        if len(selected) == 1 and selected[0] in items:
            n = items.index(selected[0]) + (1 if forward else -1)
            if not 0 <= n < len(items):
                return False
        else:
            n = 0 if forward else len(items) - 1
        self._select([items[n]])
        self.ensureVisible(items[n])
        return True

    def place_from_keyboard(self) -> bool:
        """The current tool's markup in the middle of what you can see; False if it can't be placed."""
        tool = self.tool
        if self._drag is not None:  # a drag with the pointer is under way: finish that first
            return False
        if tool in POINTER_TOOLS:
            self.notice.emit(f"Draw with the mouse or a pen: {POINTER_TOOLS[tool]} follows the pointer.")
            return False
        if tool in ("select", "crop"):
            return False
        visible = self.mapToScene(self.viewport().rect()).boundingRect().intersected(self._crop_rect)
        area = visible if not visible.isEmpty() else QRectF(self._crop_rect)
        middle = area.center()
        if tool == "text":
            self._new_text(middle)
            return True
        if tool == "step":
            self._add_step(middle)
            return True
        w = max(PLACED_MIN[0], area.width() * PLACED_SHARE)
        h = max(PLACED_MIN[1], area.height() * PLACED_SHARE)
        start, end = middle - QPointF(w / 2, h / 2), middle + QPointF(w / 2, h / 2)
        if tool in ("arrow", "line"):
            start, end = QPointF(start.x(), middle.y()), QPointF(end.x(), middle.y())
        if tool in ("stamp", "callout"):
            start = end = middle  # a click's worth: their usual size
        self._begin_create(start)
        drag, self._drag = self._drag, None
        if drag is None:
            return False
        if end != start:
            self._update_create(drag, end, False)
        self._finish_create(drag)
        return True

    def resize_selection(self, dw: float, dh: float) -> None:
        """Make the selected boxes and lines `dw` wider and `dh` taller (one undo step)."""
        records = []
        if self._only_locked():
            return
        for item in self.selected_annotations():
            if item.locked:
                continue
            before = item.get_state()
            after = dict(before)
            if "size" in before:
                min_w, min_h = getattr(item, "min_size", (6.0, 6.0))
                w, h = before["size"]
                if getattr(item, "always_square", False):  # stamps stay square: either key sizes both
                    side = max(min_w, min_h, max(w, h) + (dw or dh))
                    after["size"] = [side, side]
                else:
                    after["size"] = [max(min_w, w + dw), max(min_h, h + dh)]
            elif "p2" in before:
                x, y = before["p2"]
                after["p2"] = [x + dw, y + dh]
            else:
                continue
            if after != before:
                corner = item.mapToScene(QPointF(0, 0))
                item.set_state(after)
                if item.rotation():  # turned: grows from its corner, which stays where it is
                    item.setPos(item.pos() + corner - item.mapToScene(QPointF(0, 0)))
                records.append((item, before, item.get_state()))
        if records:
            key = ("size", tuple(id(r[0]) for r in records))  # held or repeated presses are one step, as nudges are
            self.undo_stack.push(StateCommand(records, "Size", merge_key=key))

    def crop_by_keys(self, dx: float, dy: float, resize: bool) -> None:
        """While cropping: move the crop by (dx, dy), or with `resize` make it that much bigger,
        staying inside the snip and at least MIN_CROP across."""
        crop, bounds = QRectF(self._mask.crop), self._image_rect
        if resize:
            crop.setWidth(min(bounds.right() - crop.left(), max(MIN_CROP, crop.width() + dx)))
            crop.setHeight(min(bounds.bottom() - crop.top(), max(MIN_CROP, crop.height() + dy)))
        else:
            x = min(max(bounds.left(), crop.left() + dx), bounds.right() - crop.width())
            y = min(max(bounds.top(), crop.top() + dy), bounds.bottom() - crop.height())
            crop.moveTo(x, y)
        self._mask.set_crop(crop)
        self._overlay.update()
