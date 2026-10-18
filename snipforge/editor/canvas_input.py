"""Keyboard and mouse handling for the canvas, with hit testing and hover.

Gestures, with any tool active:
  * click an annotation           -> select it (Shift/Ctrl+click adds or removes)
  * drag a selected annotation     -> move the selection
  * drag a handle                  -> resize / reshape
  * drag anywhere else             -> draw with the current tool
The Select tool adds rubber-band selection and never draws.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, Qt

from .commands import StateCommand
from .crop import MIN_CROP, crop_handle_at, move_crop, new_crop, resize_crop
from .handles import CURSORS
from .items import Annotation

DRAG_THRESHOLD = 4  # view pixels before a press counts as a drag

# Single-key tool shortcuts live here (not as QActions) so they never steal text input.
TOOL_KEYS = {
    Qt.Key_V: "select", Qt.Key_A: "arrow", Qt.Key_L: "line", Qt.Key_R: "rect",
    Qt.Key_E: "ellipse", Qt.Key_P: "pen", Qt.Key_H: "highlight", Qt.Key_T: "text",
    Qt.Key_K: "callout", Qt.Key_I: "stamp", Qt.Key_N: "step", Qt.Key_X: "pixelate",
    Qt.Key_B: "blur", Qt.Key_S: "spotlight", Qt.Key_M: "magnify", Qt.Key_C: "crop",
}
NUDGE_KEYS = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}
TEXT_TOOLS = {"text", "callout"}


class InputMixin:
    """Keyboard and mouse events for the Canvas (a QGraphicsView). The drawing steps they start
    live in DrawMixin (canvas_draw.py).

    A gesture in progress is `self._drag`, a dict whose "type" is one of: pending (pressed on an
    annotation, not yet moved), create, handle, move, crop_handle, crop_move or crop_new. Uses the
    canvas's `_scene`, `_mask`, `_overlay`, `_press_states`, `_panning`, `tool` and `undo_stack`."""

    # ------------------------------------------------------------------ keyboard
    def keyPressEvent(self, e) -> None:
        if self.picking() and e.key() == Qt.Key_Escape:
            self.stop_picking()
            return
        if self.picking() and e.key() != Qt.Key_Space and e.key() not in TOOL_KEYS:
            return  # Delete, Enter, arrows... wait until you've picked (a tool key ends picking)
        if self.editing_text_item() is not None:
            if e.key() == Qt.Key_Escape:
                self.commit_text()
            else:
                super().keyPressEvent(e)
            return
        key, mods = e.key(), e.modifiers()
        if key == Qt.Key_Space and not e.isAutoRepeat():
            self._panning = True
            self._apply_drag_mode()
            return
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selection()
            return
        if self.tool == "crop" and key in (Qt.Key_Return, Qt.Key_Enter):
            self.set_tool("select")  # leaving the tool applies the crop
            return
        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_F2):
            selected = self.selected_annotations()
            if len(selected) == 1 and hasattr(selected[0], "start_text_editing"):
                self.setFocus()
                selected[0].start_text_editing()
                return
        if key == Qt.Key_Escape:
            if self._drag is not None:
                self._cancel_drag()
            elif self.tool == "crop":
                self.cancel_crop()
                self.set_tool("select")
            elif self.selected_annotations():
                self._scene.clearSelection()
            elif self.tool != "select":
                self.set_tool("select")
            return
        if key in NUDGE_KEYS and self.selected_annotations():
            step = 10 if mods & Qt.ShiftModifier else 1
            dx, dy = NUDGE_KEYS[key]
            self.nudge(dx * step, dy * step)
            return
        tool = TOOL_KEYS.get(key)
        if tool and not mods & (Qt.ControlModifier | Qt.AltModifier):
            self.set_tool(tool)
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e) -> None:
        if e.key() == Qt.Key_Space and not e.isAutoRepeat() and self._panning:
            self._panning = False
            self._apply_drag_mode()
            return
        super().keyReleaseEvent(e)

    def focusOutEvent(self, e) -> None:
        if self._panning:  # Space release may never arrive (Alt+Tab, hotkey)
            self._panning = False
            self._apply_drag_mode()
        if e.reason() != Qt.PopupFocusReason:
            self._cancel_drag()  # the mouse release may never arrive either
            self.stop_picking()
        super().focusOutEvent(e)

    def _cancel_drag(self) -> None:
        """Abandon an in-progress gesture and put everything back as it was."""
        drag, self._drag = self._drag, None
        if drag is None:
            return
        kind = drag["type"]
        if kind == "create":
            if drag["item"].scene() is self._scene:
                self._scene.removeItem(drag["item"])
        elif kind == "handle":
            drag["item"].set_state(drag["before"])
        elif kind == "move":
            for item, before in drag["before"].items():
                item.set_state(before)
        elif kind.startswith("crop"):
            self._mask.set_crop(drag["origin_rect"])
        self._overlay.update()

    # ------------------------------------------------------------------ mouse
    def mousePressEvent(self, e) -> None:
        if self.picking() and e.button() == Qt.RightButton:
            self.stop_picking()  # right-click cancels, as in most apps
            return
        if self._panning or e.button() != Qt.LeftButton:
            super().mousePressEvent(e)
            return
        if self._drag is not None:  # a release went missing; don't orphan a half-drawn item
            self._cancel_drag()
        view_pos = e.position().toPoint()
        pos = self.mapToScene(view_pos)
        self._just_picked = False
        if self.picking():
            self._pick_press(self._pick_scene_pos(e))  # the eyedropper's click picks; it never selects or draws
            return
        editing = self.editing_text_item()
        if editing is not None:
            if self._annotation_at(view_pos) is editing.owner():
                super().mousePressEvent(e)  # place the text cursor
                return
            self.commit_text()
            if self.tool != "select":
                return  # this click only finishes the text
        if self.tool == "crop":
            self._drag = self._begin_crop_drag(pos)
            return
        handle = self._overlay.handle_at(pos, self.zoom()) or self._area_handle_at(view_pos, pos)
        if handle:
            target = self._overlay.target
            target.begin_handle_drag()
            if hasattr(target, "press_at"):
                target.press_at(pos)
            self._drag = {"type": "handle", "item": target, "name": handle, "before": target.get_state()}
            return
        if self.tool == "select":
            self._press_states = {i: i.get_state() for i in self.annotations()}
            super().mousePressEvent(e)
            return
        hit = self._annotation_at(view_pos)
        toggle = bool(e.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier))
        if hit is not None:
            if self.tool in TEXT_TOOLS and hasattr(hit, "start_text_editing") and not toggle:
                self._select([hit])
                self.setFocus()
                hit.start_text_editing()
                return
            # Decide on release (click = select) or on first move (drag = move or draw).
            self._drag = {"type": "pending", "hit": hit, "origin": pos, "view_origin": view_pos,
                          "toggle": toggle}
            return
        if self.tool == "text":
            self._new_text(pos)
        elif self.tool == "step":
            self._add_step(pos)
        else:
            self._begin_create(pos)

    def mouseMoveEvent(self, e) -> None:
        view_pos = e.position().toPoint()
        pos = self.mapToScene(view_pos)
        shift = bool(e.modifiers() & Qt.ShiftModifier)
        drag = self._drag
        if self.picking():
            if self._panning:  # Space+drag still pans
                self._loupe.hide()
                super().mouseMoveEvent(e)
            else:
                self._pick_hover(view_pos, self._pick_scene_pos(e))
            return
        if drag is None:
            if not e.buttons():
                self._update_hover(view_pos, pos)
            super().mouseMoveEvent(e)
            self._overlay.update()
            return
        if drag["type"] == "pending":
            if (view_pos - drag["view_origin"]).manhattanLength() < DRAG_THRESHOLD:
                return
            drag = self._resolve_pending(drag)
            if drag is None:
                return
        kind = drag["type"]
        if kind == "handle":
            drag["item"].drag_handle(drag["name"], pos, shift)
        elif kind == "crop_handle":
            self._mask.set_crop(resize_crop(drag["origin_rect"], drag["name"], pos, self._image_rect))
        elif kind == "crop_move":
            self._mask.set_crop(move_crop(drag["origin_rect"], pos - drag["origin"], self._image_rect))
        elif kind == "crop_new":
            self._mask.set_crop(new_crop(drag["origin"], pos, self._image_rect))
        elif kind == "move":
            delta = pos - drag["origin"]
            for item, before in drag["before"].items():
                item.setPos(QPointF(*before["pos"]) + delta)
        else:
            self._update_create(drag, pos, shift)
        self._overlay.update()

    def mouseDoubleClickEvent(self, e) -> None:
        """Qt sends the second press of a quick double-click here, not to mousePressEvent."""
        if self._panning or e.button() != Qt.LeftButton:
            super().mouseDoubleClickEvent(e)
            return
        if self._just_picked or self.picking():  # a double-click with the eyedropper is just a pick
            self._just_picked = False
            if self.picking():
                self.mousePressEvent(e)
            return
        view_pos = e.position().toPoint()
        hit = self._annotation_at(view_pos)
        editing = self.editing_text_item()
        if editing is not None and hit is editing.owner():
            super().mouseDoubleClickEvent(e)  # double-click inside text selects a word
            return
        if hit is not None and hasattr(hit, "start_text_editing"):
            self.commit_text()
            self._select([hit])
            self.setFocus()
            hit.start_text_editing()
            return
        self.mousePressEvent(e)  # otherwise it's just another press (keeps moves undoable)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.LeftButton:
            super().mouseReleaseEvent(e)
            return
        drag, self._drag = self._drag, None
        if drag is None:
            super().mouseReleaseEvent(e)
            if self.tool == "select":
                self._commit_moves(self._press_states)
                self._press_states = {}
            return
        kind = drag["type"]
        if kind == "pending":
            self._click_select(drag["hit"], drag["toggle"])
        elif kind == "handle":
            item, after = drag["item"], drag["item"].get_state()
            if after != drag["before"]:
                self.undo_stack.push(StateCommand([(item, drag["before"], after)], "Resize"))
        elif kind.startswith("crop"):
            draft = self._mask.crop
            if draft.width() < MIN_CROP or draft.height() < MIN_CROP:  # a stray click
                self._mask.set_crop(drag["origin_rect"])
        elif kind == "move":
            self._commit_moves(drag["before"])
        else:
            self._finish_create(drag)
        self._overlay.update()

    # ------------------------------------------------------------------ hit testing and hover
    def _area_handle_at(self, view_pos, scene_pos: QPointF) -> str | None:
        """Some selected items can be dragged by an area, not just a handle: inside a magnifier's
        source frame moves what it shows. Other markup on top there still gets the click."""
        target = self._overlay.target
        area = getattr(target, "area_handle_at", None) if target is not None and target.scene() else None
        handle = area(scene_pos) if area is not None else None
        if handle and self._annotation_at(view_pos) not in (None, target):
            return None
        return handle

    def _annotation_at(self, view_pos) -> Annotation | None:
        for item in self.items(view_pos):
            while item is not None and not isinstance(item, Annotation):
                item = item.parentItem()  # e.g. the text inside a callout
            if item is not None and self._visible_in_crop(item):
                return item
        return None

    def _update_hover(self, view_pos, scene_pos: QPointF) -> None:
        if self.tool == "crop":
            self._overlay.set_hover(None)
            rect = self._mask.crop
            handle = crop_handle_at(rect, scene_pos, self.zoom())
            if handle:
                self.viewport().setCursor(CURSORS[handle])
            elif rect.contains(scene_pos) and rect != self._image_rect:
                self.viewport().setCursor(Qt.SizeAllCursor)
            else:
                self.viewport().setCursor(Qt.CrossCursor)
            return
        handle = self._overlay.handle_at(scene_pos, self.zoom()) or self._area_handle_at(view_pos, scene_pos)
        hit = None if handle else self._annotation_at(view_pos)
        self._overlay.set_hover(hit)
        if handle:
            cursor = CURSORS[handle]
        elif hit is not None:
            if self.tool in TEXT_TOOLS and hasattr(hit, "start_text_editing"):
                cursor = Qt.IBeamCursor
            elif hit.isSelected() or self.tool == "select":
                cursor = Qt.SizeAllCursor
            else:
                cursor = Qt.PointingHandCursor
        else:
            cursor = Qt.ArrowCursor if self.tool == "select" else Qt.CrossCursor
        self.viewport().setCursor(cursor)

    def leaveEvent(self, e) -> None:
        self._overlay.set_hover(None)
        self._loupe.hide()
        super().leaveEvent(e)

    def viewportEvent(self, e) -> bool:  # noqa: N802 - Qt API
        if e.type() == QEvent.Leave:  # onto a scrollbar, which the view's own leaveEvent doesn't see
            self._loupe.hide()
        return super().viewportEvent(e)
