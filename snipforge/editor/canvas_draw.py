"""What the canvas's gestures do: start a crop drag, start, grow and finish new annotations,
turn a press on an annotation into a click-select, a move or a draw, and record moves for undo."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QFontMetricsF

from .app_stamp import fitted
from .commands import AddItemsCommand, StateCommand
from .crop import crop_handle_at
from .edges import DEPTH as EDGE_DEPTH
from .erase import EraseItem
from .items import (
    Annotation,
    ArrowItem,
    BoxItem,
    EffectItem,
    EllipseItem,
    HidesContent,
    LineItem,
    RectItem,
    SpotlightItem,
    StepItem,
    StrokeItem,
)
from .magnify import MIN_SOURCE, MagnifyItem, default_source, place_lens
from .stamps import DEFAULT_SIZE as STAMP_SIZE
from .stamps import StampItem
from .style import Style
from .text_items import CalloutItem, TextItem

DEFAULT_CALLOUT = (190.0, 64.0)
TEXT_STAMP_STYLE = Style(color="#1c1c1e", fill=True, font_size=12, shadow=False)  # small, dark and readable
STAMP_INSET = 10.0
MIN_STAMP_FONT, MAX_STAMP_FONT = 8, 28  # it grows with the snip: about 1 point per 100 px across
# Tools made to go over things: a drag on markup draws with them unless that markup is selected.
DRAW_OVER_TOOLS = {"highlight", "pixelate", "blur", "spotlight", "magnify", "erase", "move"}
UNDERLAY_Z = -100  # highlighter strokes and spotlights sit at or below this, under other markup


class DrawMixin:
    """Gesture steps for the Canvas. InputMixin (canvas_input.py) calls them from mouse events."""

    def set_cutout_options(self, **options) -> None:
        """direction: "vertical" (take out columns) or "horizontal" (rows); seam: "straight" or "torn"."""
        if options.get("direction") in ("vertical", "horizontal"):
            self.cutout_options = {**self.cutout_options, "direction": options["direction"]}
        if options.get("seam") in ("straight", "torn"):
            self.cutout_options = {**self.cutout_options, "seam": options["seam"]}

    def _cut_strip(self, a: QPointF, b: QPointF) -> tuple[str, int, int]:
        """The strip between two points across the whole image: (axis, start, end) in pixels."""
        width, height = self._image_rect.width(), self._image_rect.height()
        if self.cutout_options["direction"] == "vertical":
            lo, hi, length, axis = min(a.x(), b.x()), max(a.x(), b.x()), width, "x"
        else:
            lo, hi, length, axis = min(a.y(), b.y()), max(a.y(), b.y()), height, "y"
        return axis, int(min(max(round(lo), 0), length)), int(min(max(round(hi), 0), length))

    def _strip_rect(self, strip: tuple[str, int, int]) -> QRectF:
        axis, start, end = strip
        r = self._image_rect
        if axis == "x":
            return QRectF(start, r.top(), end - start, r.height())
        return QRectF(r.left(), start, r.width(), end - start)

    def _finish_cut(self, a: QPointF, b: QPointF) -> None:
        axis, start, end = self._cut_strip(a, b)
        if end - start < 2:
            return  # a click, not a cut
        try:
            self.cut_out(axis, start, end, self.cutout_options["seam"])
        except ValueError as exc:
            self.notice.emit(f"Couldn't cut that out: {exc}")

    def _begin_crop_drag(self, pos: QPointF) -> dict:
        rect = QRectF(self._mask.crop)
        handle = crop_handle_at(rect, pos, self.zoom())
        if handle:
            return {"type": "crop_handle", "name": handle, "origin_rect": rect}
        if rect.contains(pos) and rect != self._image_rect:
            return {"type": "crop_move", "origin": pos, "origin_rect": rect}
        return {"type": "crop_new", "origin": pos, "origin_rect": rect}

    def drag_moves(self, hit: Annotation) -> bool:
        """Whether a drag starting on `hit` moves it (rather than drawing over it). Selected markup always
        moves, and so does anything with the Step tool. Otherwise a drag draws with the tools made to go
        over things, and over highlights and blur, erase and spotlight areas, which read as part of the
        snip; everything else moves."""
        if hit.isSelected() or self.tool in ("select", "step"):
            return True
        underlay = hit.zValue() <= UNDERLAY_Z or isinstance(hit, (HidesContent, SpotlightItem))
        return self.tool not in DRAW_OVER_TOOLS and not underlay

    def _resolve_pending(self, drag: dict) -> dict | None:
        """A press on an annotation turned into a drag: move it, with the rest of the selection if it's
        part of one (Shift/Ctrl adds it to the selection first), or draw over it (see drag_moves)."""
        hit = drag["hit"]
        if drag.get("typing"):
            self.commit_text()  # dragging a callout you're typing in: finish the text, then move it
        if not self.drag_moves(hit):
            self._drag = None
            if self.tool == "text":
                self._new_text(drag["origin"])
            else:
                self._begin_create(drag["origin"])
            return self._drag
        if not hit.isSelected():
            if drag["toggle"]:
                hit.setSelected(True)
            else:
                self._select([hit])
        moving = self.selected_annotations()
        self._drag = {"type": "move", "origin": drag["origin"], "before": {i: i.get_state() for i in moving}}
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
        elif tool == "erase":
            item = EraseItem(style, self.base_image)
        elif tool == "move":  # Smart Move: the area dragged out is shown as it will be erased
            item = EraseItem(self.tool_styles.get("erase", style), self.base_image)
        elif tool == "spotlight":
            item = SpotlightItem(style, self._image_rect)
        elif tool == "callout":
            item = self._adopt(CalloutItem(style))
        elif tool == "stamp":
            item = StampItem(style)
        elif tool == "magnify":
            item = MagnifyItem(style, self.base_image)
            item.set_source(QRectF(pos, pos))  # you drag out what to magnify; the lens comes on release
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
        elif isinstance(item, MagnifyItem):
            dx, dy = pos.x() - origin.x(), pos.y() - origin.y()
            if shift:
                side = max(abs(dx), abs(dy))
                dx, dy = (side if dx >= 0 else -side), (side if dy >= 0 else -side)
            item.set_source(QRectF(origin, QPointF(origin.x() + dx, origin.y() + dy)))
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
        if isinstance(item, MagnifyItem):
            visible = self._crop_rect  # place the lens where you can see it
            source = item.source()
            if source.width() < MIN_SOURCE and source.height() < MIN_SOURCE:  # a click magnifies around it
                source = default_source(origin, visible)
            elif source.width() < MIN_SOURCE or source.height() < MIN_SOURCE:  # a thin drag: as thin as allowed
                grow_w, grow_h = max(0.0, MIN_SOURCE - source.width()), max(0.0, MIN_SOURCE - source.height())
                source = source.adjusted(-grow_w / 2, -grow_h / 2, grow_w / 2, grow_h / 2)
            item.set_source(source)
            item.set_scene_rect(place_lens(source, visible))
        if isinstance(item, CalloutItem) and item.is_degenerate():  # a click drops a default callout
            w, h = DEFAULT_CALLOUT
            item.set_scene_rect(QRectF(origin.x(), origin.y() - h, w, h))
            item.reset_tail()
        if item.is_degenerate():
            return
        if self.tool == "move":
            self._pick_up(item)
            return
        self.undo_stack.push(AddItemsCommand(self._scene, [item]))
        self._select([item])
        if isinstance(item, CalloutItem):
            self.setFocus()
            item.start_text_editing()

    def _pick_up(self, erase: EraseItem) -> None:
        """Smart Move: the area becomes a piece you can drag anywhere, and Smart erase fills in where
        it was (one undo step). The piece is the snip there as shown, so hidden parts stay hidden."""
        from .piece import PieceItem, default_style

        area = erase.scene_rect().toAlignedRect().intersected(self._image_rect.toAlignedRect())
        if area.isEmpty():
            return
        try:
            piece = PieceItem(default_style(), self.shown_pixels(area))
        except ValueError:  # no memory for its picture
            self.notice.emit("Couldn't pick that up: there isn't the memory for it.")
            return
        piece.set_scene_rect(QRectF(area))
        piece.setZValue(erase.zValue() + 1)  # over its erase (they'd tie: neither is in the scene yet)
        self.undo_stack.push(AddItemsCommand(self._scene, [erase, piece], "Smart Move"))
        self.set_tool("select")
        self._select([piece])
        self.notice.emit("Drag the piece where you want it. Smart erase has filled in where it was.")

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

    def add_text_stamp(self, where: str, time: str) -> None:
        """The App name and date stamp in the snip's bottom-left corner, clear of any border or torn
        edge and sized to the snip, selected so you can move or restyle it; one undo step. If it's too
        long to fit across, the window title is shortened with "…", never the date."""
        self.commit_text()
        self.set_tool("select")
        crop = self._crop_rect
        edges = self._edges
        inset = STAMP_INSET + edges.border + (EDGE_DEPTH if edges.torn & {"bottom", "left"} else 0)
        size = min(MAX_STAMP_FONT, max(TEXT_STAMP_STYLE.font_size, round(crop.width() / 100)))
        item = self._adopt(TextItem(TEXT_STAMP_STYLE.with_changes(font_size=size)))
        margin = 2 * item.document().documentMargin() + 2
        room = crop.width() - 2 * inset - margin
        while size > MIN_STAMP_FONT and QFontMetricsF(item.font()).horizontalAdvance(time) > room:
            size -= 1  # a narrow snip: smaller text, down to what's still readable
            item.set_style(TEXT_STAMP_STYLE.with_changes(font_size=size))
        item.setPlainText(fitted(where, time, QFontMetricsF(item.font()), room))
        box = item.boundingRect()
        item.setPos(crop.left() + inset, max(crop.top(), crop.bottom() - inset - box.height()))
        item.setZValue(self._next_z())
        self.undo_stack.push(AddItemsCommand(self._scene, [item], "Add app name and date"))
        self._select([item])
        self.ensureVisible(item)

    def _add_step(self, pos: QPointF) -> None:
        numbers = [i.number for i in self.annotations() if isinstance(i, StepItem)]
        item = StepItem(self.tool_styles["step"], max(numbers, default=0) + 1)
        item.setPos(pos)
        item.setZValue(self._next_z())
        self.undo_stack.push(AddItemsCommand(self._scene, [item], "Add step"))
        self._select([item])
