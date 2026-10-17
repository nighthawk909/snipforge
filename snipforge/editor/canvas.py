"""Editable canvas: the snip plus annotation items, tool gestures and undo.

Gestures, with any tool active:
  * click an annotation           -> select it (Shift/Ctrl+click adds or removes)
  * drag a selected annotation     -> move the selection
  * drag a handle                  -> resize / reshape
  * drag anywhere else             -> draw with the current tool
The Select tool adds rubber-band selection and never draws.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPixmap, QUndoStack
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView

from .. import theme
from .beautify import BeautifyStyle
from .commands import AddItemsCommand, CropCommand, RemoveItemsCommand, StateCommand
from .crop import MIN_CROP, CropMask, crop_handle_at, move_crop, new_crop, resize_crop
from .factory import create_item
from .handles import CURSORS, HandleOverlay
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
from .style import DEFAULT_TOOL_STYLES, Style
from .text_items import CalloutItem, EditableText, TextItem

DRAG_THRESHOLD = 4  # view pixels before a press counts as a drag
DUPLICATE_OFFSET = 16.0
SCENE_MARGIN = 60.0
DOT_GAP = 18  # on-screen pixels between background dots
MIN_ZOOM, MAX_ZOOM = 0.05, 16.0
DEFAULT_CALLOUT = (190.0, 64.0)

# Single-key tool shortcuts live here (not as QActions) so they never steal text input.
TOOL_KEYS = {
    Qt.Key_V: "select", Qt.Key_A: "arrow", Qt.Key_L: "line", Qt.Key_R: "rect",
    Qt.Key_E: "ellipse", Qt.Key_P: "pen", Qt.Key_H: "highlight", Qt.Key_T: "text",
    Qt.Key_K: "callout", Qt.Key_I: "stamp", Qt.Key_N: "step", Qt.Key_X: "pixelate",
    Qt.Key_B: "blur", Qt.Key_S: "spotlight", Qt.Key_C: "crop",
}
NUDGE_KEYS = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}
TEXT_TOOLS = {"text", "callout"}


class Canvas(QGraphicsView):
    tool_changed = Signal(str)
    selection_changed = Signal()
    zoom_changed = Signal(float)
    document_changed = Signal()

    def __init__(self, image: QImage, tool_styles: dict[str, Style] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.base_image = image
        self.undo_stack = QUndoStack(self)
        self.tool = "select"
        self.tool_styles: dict[str, Style] = {**DEFAULT_TOOL_STYLES, **(tool_styles or {})}
        self.closing = False

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._image_rect = QRectF(image.rect())
        self._scene.setSceneRect(self._image_rect.adjusted(-SCENE_MARGIN, -SCENE_MARGIN,
                                                           SCENE_MARGIN, SCENE_MARGIN))
        # Keep a Python reference: PySide can otherwise delete a Python-created item once a
        # temporary wrapper of it (e.g. from items()) is garbage-collected.
        self._base_item = QGraphicsPixmapItem(QPixmap.fromImage(image))
        self._base_item.setZValue(-1000)
        self._base_item.setAcceptedMouseButtons(Qt.NoButton)
        self._scene.addItem(self._base_item)
        self._crop_rect = QRectF(self._image_rect)
        self._mask = CropMask(self._image_rect, QColor(theme.current().palette.well))
        self._scene.addItem(self._mask)
        self._overlay = HandleOverlay(self._scene.sceneRect())
        self._scene.addItem(self._overlay)

        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setBackgroundBrush(QColor(theme.current().palette.well))
        self._dot_tiles: dict[float, QPixmap] = {}
        self._mask.set_pattern(self._background_pattern)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setMouseTracking(True)

        self._drag: dict | None = None
        self._press_states: dict = {}
        self._panning = False
        self._unparsed: list[dict] = []  # saved items this version couldn't load; written back untouched

        self._scene.selectionChanged.connect(self._on_selection_changed)
        theme.on_change(self.retheme)
        self.undo_stack.indexChanged.connect(self._on_undo_index)
        self._apply_drag_mode()

    # ------------------------------------------------------------------ queries
    def annotations(self) -> list[Annotation]:
        return [i for i in self._scene.items() if isinstance(i, Annotation)]

    def selected_annotations(self) -> list[Annotation]:
        return [i for i in self._scene.selectedItems() if isinstance(i, Annotation)]

    def editing_text_item(self) -> EditableText | None:
        item = self._scene.focusItem()
        return item if isinstance(item, EditableText) and item.is_editing() else None

    def crop_rect(self) -> QRectF:
        return QRectF(self._crop_rect)

    def frame_rect(self) -> QRectF:
        """What an export covers: the crop, plus Beautify's padding when it's on."""
        style = self._mask.beautify
        pad = style.padding if style is not None else 0
        return self._crop_rect.adjusted(-pad, -pad, pad, pad)

    def zoom(self) -> float:
        return self.transform().m11() or 1.0

    def style_target(self) -> tuple[str, Style | None]:
        """What the property bar should show: the selection, else the active tool."""
        selected = self.selected_annotations()
        if selected:
            return selected[0].kind, selected[0].style
        return self.tool, self.tool_styles.get(self.tool)

    # ------------------------------------------------------------------ tools and style
    def set_tool(self, tool: str) -> None:
        self.commit_text()
        if tool == "crop" and self.tool == "crop" and self._mask.editing:
            return  # re-choosing Crop mid-crop keeps your adjustments
        if self.tool == "crop" and tool != "crop":
            self.apply_crop()  # switching tools keeps the crop, like Snipping Tool
        self.tool = tool
        if tool != "select":
            self._scene.clearSelection()
        self._apply_drag_mode()
        self.viewport().setCursor(Qt.ArrowCursor if tool == "select" else Qt.CrossCursor)
        if tool == "crop":
            self._enter_crop()
        self.tool_changed.emit(tool)
        self.selection_changed.emit()

    def _apply_drag_mode(self) -> None:
        if self._panning:
            self.setDragMode(QGraphicsView.ScrollHandDrag)
        elif self.tool == "select":
            self.setDragMode(QGraphicsView.RubberBandDrag)
        else:
            self.setDragMode(QGraphicsView.NoDrag)

    def apply_style(self, **changes) -> None:
        selected = self.selected_annotations()
        for kind in {i.kind for i in selected} or {self.tool}:
            if kind in self.tool_styles:
                self.tool_styles[kind] = self.tool_styles[kind].with_changes(**changes)
        editing = self.editing_text_item()
        editing_owner = editing.owner() if editing is not None else None
        records = []
        for item in selected:
            if item is editing_owner or getattr(item, "is_new", False):
                # Text mid-edit: the edit's own Add/Edit undo step captures the style change.
                item.set_style(item.style.with_changes(**changes))
                continue
            before = item.get_state()
            item.set_style(item.style.with_changes(**changes))
            records.append((item, before, item.get_state()))
        if records:
            key = ("style", tuple(id(r[0]) for r in records), tuple(sorted(changes)))
            self.undo_stack.push(StateCommand(records, "Style", merge_key=key))
        self._overlay.update()
        self.selection_changed.emit()

    # ------------------------------------------------------------------ edits
    def delete_selection(self) -> None:
        items = self.selected_annotations()
        if items:
            self.undo_stack.push(RemoveItemsCommand(self._scene, items))

    def duplicate_selection(self) -> None:
        sources = self.selected_annotations()
        if not sources:
            return
        copies = []
        for src in sources:
            state = src.get_state()
            state["pos"] = [state["pos"][0] + DUPLICATE_OFFSET, state["pos"][1] + DUPLICATE_OFFSET]
            if src.zValue() > -100:  # spotlights/highlighters keep their layer below annotations
                state["z"] = self._next_z()
            copies.append(self._adopt(create_item(state, self.base_image, self._image_rect)))
        self.undo_stack.push(AddItemsCommand(self._scene, copies, "Duplicate"))
        self._select(copies)

    def nudge(self, dx: float, dy: float) -> None:
        records = []
        for item in self.selected_annotations():
            before = item.get_state()
            item.moveBy(dx, dy)
            records.append((item, before, item.get_state()))
        if records:
            key = ("nudge", tuple(id(r[0]) for r in records))
            self.undo_stack.push(StateCommand(records, "Nudge", merge_key=key))
            self._overlay.update()

    def reorder_selection(self, to_front: bool) -> None:
        items = self.selected_annotations()
        if not items:
            return
        zs = [i.zValue() for i in self.annotations()]
        z = (max(zs) + 1) if to_front else (min(zs) - 1)
        records = []
        for item in items:
            before = item.get_state()
            item.setZValue(z)
            records.append((item, before, item.get_state()))
        self.undo_stack.push(StateCommand(records, "Bring to front" if to_front else "Send to back"))

    def select_all(self) -> None:
        if self.editing_text_item() is None:
            for item in self.annotations():
                if self._visible_in_crop(item):
                    item.setSelected(True)

    def _visible_in_crop(self, item) -> bool:
        return self._mask.editing or item.sceneBoundingRect().intersects(self._crop_rect)

    def add_redactions(self, rects: list[QRectF], mode: str = "blur") -> int:
        style = self.tool_styles[mode]
        items = []
        for rect in rects:
            item = EffectItem(style, self.base_image, mode)
            item.set_scene_rect(rect.adjusted(-3, -3, 3, 3))
            item.setZValue(self._next_z())
            items.append(item)
        if items:
            self.undo_stack.push(AddItemsCommand(self._scene, items, "Auto-redact"))
        return len(items)

    def set_crop_rect(self, rect: QRectF) -> None:
        self._crop_rect = QRectF(rect)
        self._mask.set_crop(rect)
        self._update_scene_rect()

    def set_beautify(self, style: BeautifyStyle | None) -> None:
        """Preview Beautify around the snip (never exported: render_image hides the mask). If you
        could see the whole snip, you still can; a zoomed-in view stays as it is."""
        whole = self.mapToScene(self.viewport().rect()).boundingRect().contains(self.frame_rect())
        self._mask.set_beautify(style)
        self._update_scene_rect()
        if whole and not self._mask.editing:
            self.fit_image()

    def reset_crop(self) -> None:
        if self._crop_rect != self._image_rect:
            self.undo_stack.push(CropCommand(self, self._crop_rect, self._image_rect))

    # ------------------------------------------------------------------ crop mode
    def crop_editing(self) -> bool:
        return self._mask.editing

    def _enter_crop(self) -> None:
        self._scene.clearSelection()
        self._mask.set_crop(self._crop_rect)
        self._mask.set_editing(True)
        self._update_scene_rect()
        self.fit_rect(self._image_rect)  # show everything so cut areas can be brought back

    def apply_crop(self) -> None:
        if not self._mask.editing:
            return
        if self._drag is not None and self._drag["type"].startswith("crop"):
            self._drag = None  # Enter or a tool key mid-drag: keep the crop as it is now
        draft = QRectF(self._mask.crop)
        self._mask.set_editing(False)
        if draft != self._crop_rect and draft.width() >= MIN_CROP and draft.height() >= MIN_CROP:
            self.undo_stack.push(CropCommand(self, self._crop_rect, draft))
        else:
            self._mask.set_crop(self._crop_rect)
        self._update_scene_rect()
        self.fit_image()

    def cancel_crop(self) -> None:
        if not self._mask.editing:
            return
        if self._drag is not None and self._drag["type"].startswith("crop"):
            self._drag = None
        self._mask.set_editing(False)
        self._mask.set_crop(self._crop_rect)
        self._update_scene_rect()
        self.fit_image()

    def reset_crop_draft(self) -> None:
        """In crop mode: bring back the whole screenshot (kept when you apply)."""
        if self._mask.editing:
            self._mask.set_crop(self._image_rect)

    def _update_scene_rect(self) -> None:
        base = self._image_rect if self._mask.editing else self.frame_rect()
        self._scene.setSceneRect(base.adjusted(-SCENE_MARGIN, -SCENE_MARGIN, SCENE_MARGIN, SCENE_MARGIN))

    def commit_text(self) -> None:
        editing = self.editing_text_item()
        if editing is not None:
            # Finish explicitly: scene.clearFocus() is a no-op when the window isn't active.
            editing.finish_editing()
            editing.clearFocus()

    # ------------------------------------------------------------------ document
    def to_document(self) -> dict:
        items = sorted(self.annotations(), key=lambda i: i.zValue())
        r = self._crop_rect
        states = [i.get_state() for i in items if not i.is_degenerate()]
        return {"version": 1, "crop": [r.x(), r.y(), r.width(), r.height()],
                "items": states + self._unparsed}

    def load_document(self, doc: dict) -> None:
        for state in doc.get("items", []):
            try:
                self._scene.addItem(self._adopt(create_item(state, self.base_image, self._image_rect)))
            except (KeyError, TypeError, ValueError, AttributeError):
                self._unparsed.append(state)  # keep it so saving never destroys data we can't read
        try:
            crop = QRectF(*doc["crop"]).intersected(self._image_rect) if doc.get("crop") else None
        except (TypeError, ValueError):
            crop = None
        if crop is not None and not crop.isEmpty():
            self.set_crop_rect(crop)
        self.undo_stack.clear()

    def render_image(self, commit: bool = True, use_draft: bool = True) -> QImage:
        """Export the snip. While cropping, `use_draft` exports what's on screen; autosave passes
        False so the library always matches the saved document."""
        if commit:
            self.commit_text()
        crop = self._mask.crop if (self._mask.editing and use_draft) else self._crop_rect
        rect = crop.toAlignedRect()
        out = QImage(rect.size(), QImage.Format_ARGB32_Premultiplied)
        out.fill(Qt.transparent)
        self._mask.setVisible(False)
        self._overlay.setVisible(False)
        p = QPainter(out)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        self._scene.render(p, QRectF(out.rect()), QRectF(rect))
        p.end()
        self._mask.setVisible(True)
        self._overlay.setVisible(True)
        return out

    # ------------------------------------------------------------------ view
    def fit_image(self) -> None:
        self.fit_rect(self._image_rect if self._mask.editing else self.frame_rect())

    def fit_rect(self, rect: QRectF) -> None:
        self.fitInView(rect.adjusted(-24, -24, 24, 24), Qt.KeepAspectRatio)
        if self.zoom() > 1.0:
            self.resetTransform()
        self.zoom_changed.emit(self.zoom())

    def set_zoom(self, zoom: float) -> None:
        self.resetTransform()
        self.scale(zoom, zoom)
        self.zoom_changed.emit(self.zoom())

    def wheelEvent(self, e) -> None:
        if e.modifiers() & Qt.ControlModifier:
            factor = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
            new_zoom = self.zoom() * factor
            if MIN_ZOOM <= new_zoom <= MAX_ZOOM:
                self.scale(factor, factor)
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

    # ------------------------------------------------------------------ keyboard
    def keyPressEvent(self, e) -> None:
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
        if self._panning or e.button() != Qt.LeftButton:
            super().mousePressEvent(e)
            return
        if self._drag is not None:  # a release went missing; don't orphan a half-drawn item
            self._cancel_drag()
        view_pos = e.position().toPoint()
        pos = self.mapToScene(view_pos)
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
        handle = self._overlay.handle_at(pos, self.zoom())
        if handle:
            target = self._overlay.target
            target.begin_handle_drag()
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

    # ------------------------------------------------------------------ gesture helpers
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

    def _adopt(self, item):
        if hasattr(item, "on_edit_finished"):
            item.on_edit_finished = self._on_text_finished
        return item

    def _select(self, items) -> None:
        self._scene.clearSelection()
        for item in items:
            item.setSelected(True)

    def _next_z(self) -> float:
        zs = [i.zValue() for i in self.annotations() if i.zValue() > -100]
        return max(zs, default=0) + 1

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
        handle = self._overlay.handle_at(scene_pos, self.zoom())
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
        super().leaveEvent(e)

    def _on_selection_changed(self) -> None:
        if self.closing:
            return
        self._overlay.set_selection(self.selected_annotations())
        self.selection_changed.emit()

    def _on_undo_index(self, _index: int) -> None:
        self._overlay.update()
        self.selection_changed.emit()
        self.document_changed.emit()
