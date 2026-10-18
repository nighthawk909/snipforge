"""What the canvas's gestures do: start, grow and finish new annotations, turn a press into a
move or a draw, and record moves and text edits for undo."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF

from .commands import AddItemsCommand, RemoveItemsCommand, StateCommand
from .crop import crop_handle_at
from .items import (
    Annotation,
    ArrowItem,
    BoxItem,
    EffectItem,
    EllipseItem,
    LineItem,
    RectItem,
    SpotlightItem,
    StepItem,
    StrokeItem,
)
from .stamps import DEFAULT_SIZE as STAMP_SIZE
from .stamps import StampItem
from .text_items import CalloutItem, TextItem

DEFAULT_CALLOUT = (190.0, 64.0)


class DrawMixin:
    """Gesture steps for the Canvas. InputMixin (canvas_input.py) calls them from mouse events."""

    def _begin_crop_drag(self, pos: QPointF) -> dict:
        rect = QRectF(self._mask.crop)
        handle = crop_handle_at(rect, pos, self.zoom())
        if handle:
            return {"type": "crop_handle", "name": handle, "origin_rect": rect}
        if rect.contains(pos) and rect != self._image_rect:
            return {"type": "crop_move", "origin": pos, "origin_rect": rect}
        return {"type": "crop_new", "origin": pos, "origin_rect": rect}

    def _resolve_pending(self, drag: dict) -> dict | None:
        """A press on an annotation turned into a drag: move it if selected, otherwise draw."""
        hit = drag["hit"]
        if hit.isSelected() or self.tool == "step":
            if not hit.isSelected():
                self._select([hit])
            moving = self.selected_annotations()
            self._drag = {"type": "move", "origin": drag["origin"],
                          "before": {i: i.get_state() for i in moving}}
        elif self.tool == "text":
            self._drag = None
            self._new_text(drag["origin"])
        else:
            self._drag = None
            self._begin_create(drag["origin"])
        return self._drag

    def _click_select(self, hit: Annotation, toggle: bool) -> None:
        if toggle:
            hit.setSelected(not hit.isSelected())
        else:
            self._select([hit])

    def _begin_create(self, pos: QPointF) -> None:
        tool, style = self.tool, self.tool_styles.get(self.tool)
        if tool in ("arrow", "line"):
            item = (ArrowItem if tool == "arrow" else LineItem)(style, pos)
        elif tool in ("pen", "highlight"):
            item = StrokeItem(style, highlighter=tool == "highlight", start=pos)
        elif tool in ("rect", "ellipse"):
            item = (RectItem if tool == "rect" else EllipseItem)(style)
        elif tool in ("pixelate", "blur"):
            item = EffectItem(style, self.base_image, tool)
        elif tool == "spotlight":
            item = SpotlightItem(style, self._image_rect)
        elif tool == "callout":
            item = self._adopt(CalloutItem(style))
        elif tool == "stamp":
            item = StampItem(style)
        else:
            return
        if isinstance(item, BoxItem):
            item.set_scene_rect(QRectF(pos, pos))
        if item.zValue() == 0:
            item.setZValue(self._next_z())
        self._scene.clearSelection()
        self._scene.addItem(item)
        self._drag = {"type": "create", "item": item, "origin": pos}

    def _update_create(self, drag: dict, pos: QPointF, shift: bool) -> None:
        item, origin = drag["item"], drag["origin"]
        if isinstance(item, LineItem):
            item.set_end_scene(pos, shift)
        elif isinstance(item, StrokeItem):
            item.extend(item.mapFromScene(pos))
        elif isinstance(item, BoxItem):
            dx, dy = pos.x() - origin.x(), pos.y() - origin.y()
            if item.always_square or (shift and item.keep_square_with_shift):
                side = max(abs(dx), abs(dy))
                dx, dy = (side if dx >= 0 else -side), (side if dy >= 0 else -side)
            item.set_scene_rect(QRectF(origin, QPointF(origin.x() + dx, origin.y() + dy)))
            if isinstance(item, CalloutItem):
                item.reset_tail()

    def _finish_create(self, drag: dict) -> None:
        item, origin = drag["item"], drag["origin"]
        self._scene.removeItem(item)
        if isinstance(item, StampItem) and item.is_degenerate():  # a click drops a default-size stamp
            half = STAMP_SIZE / 2
            item.set_scene_rect(QRectF(origin.x() - half, origin.y() - half, STAMP_SIZE, STAMP_SIZE))
        if isinstance(item, CalloutItem) and item.is_degenerate():  # a click drops a default callout
            w, h = DEFAULT_CALLOUT
            item.set_scene_rect(QRectF(origin.x(), origin.y() - h, w, h))
            item.reset_tail()
        if item.is_degenerate():
            return
        self.undo_stack.push(AddItemsCommand(self._scene, [item]))
        self._select([item])
        if isinstance(item, CalloutItem):
            self.setFocus()
            item.start_text_editing()

    def _commit_moves(self, befores: dict) -> None:
        records = [(i, before, i.get_state()) for i, before in befores.items()
                   if i.scene() is self._scene and i.pos() != QPointF(*before["pos"])]
        if records:
            self.undo_stack.push(StateCommand(records, "Move"))

    def _new_text(self, pos: QPointF) -> None:
        item = self._adopt(TextItem(self.tool_styles["text"]))
        item.is_new = True
        item.setPos(pos.x() - 8, pos.y() - item.boundingRect().height() / 2)
        item.setZValue(self._next_z())
        self._scene.addItem(item)
        self._select([item])
        self.setFocus()
        item.start_editing()

    def _on_text_finished(self, item, before: dict | None) -> None:
        if self.closing:
            return
        if item.is_new:
            item.is_new = False
            if item.is_degenerate():
                self._scene.removeItem(item)
            else:
                self.undo_stack.push(AddItemsCommand(self._scene, [item], "Add text"))
            return
        if item.is_degenerate() and before is not None:
            item.set_state(before)
            self.undo_stack.push(RemoveItemsCommand(self._scene, [item]))
            return
        after = item.get_state()
        if before is not None and after != before:
            self.undo_stack.push(StateCommand([(item, before, after)], "Edit text"))

    def _add_step(self, pos: QPointF) -> None:
        numbers = [i.number for i in self.annotations() if isinstance(i, StepItem)]
        item = StepItem(self.tool_styles["step"], max(numbers, default=0) + 1)
        item.setPos(pos)
        item.setZValue(self._next_z())
        self.undo_stack.push(AddItemsCommand(self._scene, [item], "Add step"))
        self._select([item])
