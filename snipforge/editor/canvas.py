"""Editable canvas: the snip plus annotation items, tools, crop, undo and export.

The canvas is split by concern: canvas_input.py handles the keyboard and mouse, canvas_draw.py what
those gestures draw, and canvas_view.py zoom, fit and the background.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QUndoStack
from PySide6.QtWidgets import QApplication, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView

from .. import theme
from ..wording import keys
from . import colors
from .beautify import BeautifyStyle
from .canvas_choose import ChooseMixin
from .canvas_clipboard import ClipboardMixin
from .canvas_draw import DrawMixin
from .canvas_image import ImageMixin
from .canvas_input import InputMixin
from .canvas_keys import KeyboardMixin
from .canvas_pan import PanMixin
from .canvas_pick import PickMixin
from .canvas_view import MAX_ZOOM as MAX_ZOOM  # re-exported: the window's zoom buttons use these
from .canvas_view import MIN_ZOOM as MIN_ZOOM
from .canvas_view import SCENE_MARGIN, ViewMixin
from .commands import AddItemsCommand, CropCommand, RemoveItemsCommand, StateCommand
from .crop import MIN_CROP, CropMask
from .edges import CutPreview, EdgeItem, Edges
from .eyedropper import Loupe
from .factory import create_item
from .handles import HandleOverlay, OwnRubberBand
from .image_ops import ImageGeometry
from .items import Annotation, EffectItem
from .mini_toolbar import MiniToolbar
from .style import DEFAULT_TOOL_STYLES, LOOK_FIELDS, LOOK_KINDS, STYLE_FIELDS, VERSION_3_KINDS, Style, needs_version_3
from .text_items import EditableText, TextItem

DUPLICATE_OFFSET = 16.0
DOCUMENT_VERSION = 3  # documents from newer versions are opened read-only (see load_document)
# 2: moved pieces (Smart Move) and regular-weight text (Edit text), which version 1 can't show
# 3: tool variants (style.py: arrow heads and curves, shapes, step styles, highlight boxes, text formatting,
#    rounded redactions, spotlight dimming), which version 2 draws plain


class UnsupportedDocument(ValueError):
    """Saved by a newer SnipForge, in a way this version can't show correctly."""


def _edges(item) -> QRectF:
    """What align and distribute line up: a box's own edges, or for anything else (and anything
    turned) what it covers on the snip."""
    return item.scene_rect() if hasattr(item, "scene_rect") and not item.rotation() else item.sceneBoundingRect()


def document_version(states: list[dict]) -> int:
    """The oldest document version that shows these items right: older SnipForges still open
    every snip that doesn't need a newer one."""
    version = 1
    for state in states:
        if not isinstance(state, dict):  # kept unread from a damaged document
            continue
        style = state.get("style")
        if state.get("kind") in VERSION_3_KINDS or needs_version_3(style, state.get("kind")) \
                or state.get("locked") or state.get("hidden"):
            return 3
        if state.get("kind") == "piece" or (isinstance(style, dict) and style.get("bold") is False):
            version = 2
    return version


def _shows_snip(item) -> bool:
    """Part of how the snip itself looks (what hides parts of it, and moved pieces of it)."""
    return getattr(item, "hides_content", False) or getattr(item, "is_snip", False)


class Canvas(KeyboardMixin, InputMixin, PanMixin, DrawMixin, ClipboardMixin, PickMixin, ChooseMixin, ImageMixin,
             ViewMixin, QGraphicsView):
    tool_changed = Signal(str)
    selection_changed = Signal()
    zoom_changed = Signal(float)
    document_changed = Signal()
    picking_changed = Signal(bool)  # the eyedropper started or ended
    choosing_changed = Signal(bool)  # Edit text started or stopped offering lines to click
    color_picked = Signal(str)  # "#rrggbb", now the selection's or the tool's color
    notice = Signal(str)  # something to tell you in the status bar
    copy_requested = Signal()  # the right-click menu's Copy: the window copies (the snip, with Beautify)

    def __init__(self, image: QImage, tool_styles: dict[str, Style] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.original_image = image  # as captured; never changed
        self.base_image = image  # what you mark up: the original turned, flipped or resized
        self._geometry = ImageGeometry.of(image)
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
        band_style = OwnRubberBand(QApplication.style().name())  # the app's style (Fusion), not the desktop's
        band_style.setParent(self)  # Qt doesn't own a widget's style; the canvas does
        self.viewport().setStyle(band_style)  # QGraphicsView draws the band with its viewport's style
        self.rubberBandChanged.connect(self._on_rubber_band)
        self._edges = Edges()  # border and torn sides: part of the snip, above all markup
        self._edge_item = EdgeItem(self._mask.fill_behind)
        self._scene.addItem(self._edge_item)
        self._edge_item.set_edges(self._crop_rect, self._edges)
        self._cut_preview = CutPreview()  # the strip a Cut out drag will take away
        self._scene.addItem(self._cut_preview)
        self.cutout_options = {"direction": "vertical", "seam": "straight"}

        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setBackgroundBrush(QColor(theme.current().palette.well))
        self._dot_tiles: dict[float, QPixmap] = {}
        self._mask.set_pattern(self._background_pattern)
        self.setFrameShape(QGraphicsView.NoFrame)
        # No scroll bars: drag the empty area (or with the middle button, or Space) and the wheel move it.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)  # a bigger window keeps what's in its middle
        self.setMouseTracking(True)

        self._drag: dict | None = None
        self._press_states: dict = {}
        self._panning = False
        self._unparsed: list[dict] = []  # saved items this version couldn't load; written back untouched
        self._pick_source: QImage | None = None  # the snip as you see it, while the eyedropper is on
        self._just_picked = False
        self._skip_menu = False  # this right-click cancelled the eyedropper: no menu for it
        self._pick_point: QPointF | None = None  # the pixel the loupe shows (the arrow keys move it)
        self._pick_by_key = False  # the keys placed it (not the pointer), so zooming keeps it
        self._refreshing = False
        self._loupe = Loupe(self.viewport())
        self._mini = MiniToolbar(self.viewport())
        self._mini.color_picked.connect(lambda color: self.apply_style(color=color))
        self._mini.duplicate.connect(self.duplicate_selection)
        self._mini.forward.connect(lambda: self.reorder_selection(True))
        self._mini.backward.connect(lambda: self.reorder_selection(False))
        self._mini.lock.connect(lambda on: self.set_locked(self.selected_annotations(), on))
        self._mini.delete.connect(self.delete_selection)
        self._mini.set_swatches(colors.palette())
        colors.notifier().changed.connect(self._palette_changed)  # a method: let go when the canvas goes

        self._scene.selectionChanged.connect(self._on_selection_changed)
        theme.on_change(self.retheme)
        self.undo_stack.indexChanged.connect(self._on_undo_index)
        for bar in (self.horizontalScrollBar(), self.verticalScrollBar()):
            bar.valueChanged.connect(self._refresh_loupe)
            bar.valueChanged.connect(self.refresh_mini_toolbar)
        self.zoom_changed.connect(self._refresh_loupe)
        self.zoom_changed.connect(self.refresh_mini_toolbar)
        self.selection_changed.connect(self.refresh_mini_toolbar)
        self.document_changed.connect(self.refresh_mini_toolbar)
        self._apply_drag_mode()
        self.setAccessibleName("Snip")
        self.setAccessibleDescription(
            "The snip and its markup. Tab and Shift+Tab select the markup one at a time; the arrow keys "
            f"move it, {keys('Ctrl')}+arrow keys size it, Delete removes it and Enter edits its text. With a "
            "drawing tool, Enter adds its markup in the middle. F6 goes to the tools, the panel and the "
            "command bar.")

    # ------------------------------------------------------------------ queries
    def annotations(self) -> list[Annotation]:
        return [i for i in self._scene.items() if isinstance(i, Annotation)]

    def selected_annotations(self) -> list[Annotation]:
        return [i for i in self._scene.selectedItems() if isinstance(i, Annotation)]

    def bottom_up(self, items) -> list[Annotation]:
        """`items` in the order they're drawn, bottom first. Items on the same layer are drawn in
        the order they were added, so saving, copying or duplicating them in this order keeps what's
        on top on top (an Edit text's text over its erase, say)."""
        order = {id(item): n for n, item in enumerate(reversed(self._scene.items()))}
        return sorted(items, key=lambda item: order.get(id(item), -1))

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

    def style_target(self) -> tuple[str, Style | None]:
        """What the property bar should show: the selection, else the active tool."""
        selected = self.selected_annotations()
        if selected:
            return selected[0].kind, selected[0].style
        return self.tool, self.tool_styles.get(self.tool)

    # ------------------------------------------------------------------ tools and style
    def set_tool(self, tool: str) -> None:
        self.stop_picking()
        self.stop_choosing()
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

        def own(kind: str) -> dict:
            """The changes `kind` takes: what hides the snip never takes opacity or rotation."""
            return changes if kind in LOOK_KINDS else {k: v for k, v in changes.items() if k not in LOOK_FIELDS}

        for kind in {i.kind for i in selected} or {self.tool}:
            if kind in self.tool_styles and own(kind):
                self.tool_styles[kind] = self.tool_styles[kind].with_changes(**own(kind))
        editing = self.editing_text_item()
        editing_owner = editing.owner() if editing is not None else None
        renumbered = self.numbers_in(changes["numbering"], selected) if "numbering" in changes else {}
        records = []
        for item in selected:
            mine = own(item.kind)
            if not mine:
                continue
            if item is editing_owner or getattr(item, "is_new", False):
                # Text mid-edit: the edit's own Add/Edit undo step captures the style change.
                item.set_style(item.style.with_changes(**mine))
                continue
            before = item.get_state()
            item.set_style(item.style.with_changes(**mine))
            if item in renumbered:
                item.set_state({**item.get_state(), "number": renumbered[item]})
            records.append((item, before, item.get_state()))
        if records:
            key = ("style", tuple(id(r[0]) for r in records), tuple(sorted(changes)))
            self.undo_stack.push(StateCommand(records, "Style", merge_key=key))
        self._overlay.update()
        self.selection_changed.emit()

    # ------------------------------------------------------------------ edits
    def _only_locked(self) -> bool:
        """Whether everything selected is locked, saying so: nothing to move, size or delete."""
        selected = self.selected_annotations()
        if selected and all(i.locked for i in selected):
            self.notice.emit("It's locked: unlock it first (right-click, or the Layers list)")
            return True
        return False

    def delete_selection(self) -> None:
        items = [i for i in self.selected_annotations() if not i.locked]
        if items:
            self.remove_items(items)
        else:
            self._only_locked()

    def remove_items(self, items, text: str = "Delete") -> None:
        """Remove markup as one undo step; step numbers after removed ones close up the gap."""
        renumber = self.step_gap_changes(items)
        if not renumber:
            self.undo_stack.push(RemoveItemsCommand(self._scene, items, text))
            return
        self.undo_stack.beginMacro(text)
        self.undo_stack.push(RemoveItemsCommand(self._scene, items, text))
        self.undo_stack.push(StateCommand(renumber, "Renumber steps", already_applied=False))
        self.undo_stack.endMacro()

    def _state_change(self, items, text: str, change) -> None:
        """Apply `change(item)` to each of `items` as one undo step."""
        records = []
        for item in items:
            before = item.get_state()
            change(item)
            after = item.get_state()
            if after != before:
                records.append((item, before, after))
        if records:
            self.undo_stack.push(StateCommand(records, text))
            self._overlay.update()
            self.selection_changed.emit()

    def set_locked(self, items, on: bool) -> None:
        """Lock markup so it can't be moved, resized or deleted (or unlock it)."""
        self._state_change(items, "Lock" if on else "Unlock", lambda i: i.set_locked(on))

    def set_hidden(self, items, on: bool) -> None:
        """Hide markup (it stays in the snip's document, out of the picture) or show it again."""
        self._state_change(items, "Hide" if on else "Show", lambda i: i.set_hidden(on))

    def align_selection(self, how: str) -> None:
        """Line the selected markup up: left, center, right, top, middle or bottom. Two or more
        align to each other's edges; one aligns to the snip's."""
        items = [i for i in self.selected_annotations() if not i.locked]
        if not items:
            return
        rects = {i: _edges(i) for i in items}
        frame = self._crop_rect if len(items) == 1 else QRectF(rects[items[0]])
        for r in rects.values():
            if len(items) > 1:
                frame = frame.united(r)
        def shift(item):
            r = rects[item]
            dx = {"left": frame.left() - r.left(), "center": frame.center().x() - r.center().x(),
                  "right": frame.right() - r.right()}.get(how, 0.0)
            dy = {"top": frame.top() - r.top(), "middle": frame.center().y() - r.center().y(),
                  "bottom": frame.bottom() - r.bottom()}.get(how, 0.0)
            item.moveBy(dx, dy)
        self._state_change(items, "Align", shift)

    def distribute_selection(self, axis: str) -> None:
        """Space three or more pieces of markup evenly between the two outermost, left to right or
        top to bottom."""
        items = [i for i in self.selected_annotations() if not i.locked]
        if len(items) < 3:
            return
        rects = {i: _edges(i) for i in items}
        horizontal = axis in ("horizontal", "h")
        key = (lambda r: r.left()) if horizontal else (lambda r: r.top())
        order = sorted(items, key=lambda i: key(rects[i]))
        first, last = rects[order[0]], rects[order[-1]]
        span = (last.right() - first.left()) if horizontal else (last.bottom() - first.top())
        total = sum((rects[i].width() if horizontal else rects[i].height()) for i in order)
        gap = (span - total) / (len(order) - 1)
        targets = {}
        at = first.left() if horizontal else first.top()
        for item in order:
            targets[item] = at
            at += (rects[item].width() if horizontal else rects[item].height()) + gap
        def shift(item):
            r = rects[item]
            if horizontal:
                item.moveBy(targets[item] - r.left(), 0)
            else:
                item.moveBy(0, targets[item] - r.top())
        self._state_change(items, "Distribute", shift)

    def use_selection_as_default(self) -> None:
        """The selected markup's look becomes what its tool draws next."""
        for item in self.selected_annotations():
            if item.kind in self.tool_styles:
                self.tool_styles[item.kind] = item.style
        self.selection_changed.emit()

    def reset_tool_style(self, kind: str) -> None:
        if kind in DEFAULT_TOOL_STYLES:
            self.tool_styles[kind] = DEFAULT_TOOL_STYLES[kind]
            self.selection_changed.emit()

    def duplicate_selection(self) -> None:
        sources = self.bottom_up(self.selected_annotations())
        if not sources:
            return
        copies, top = [], self._next_z()
        for src in sources:
            state = src.get_state()
            state["pos"] = [state["pos"][0] + DUPLICATE_OFFSET, state["pos"][1] + DUPLICATE_OFFSET]
            if src.zValue() > -100:  # spotlights/highlighters keep their layer below annotations
                state["z"] = top
                top += 1  # each copy over the one below it, as the originals are
            copies.append(self._adopt(create_item(state, self.base_image, self._image_rect)))
        self.undo_stack.push(AddItemsCommand(self._scene, copies, "Duplicate"))
        self._select(copies)

    def nudge(self, dx: float, dy: float) -> None:
        records = []
        if self._only_locked():
            return
        for item in self.selected_annotations():
            if item.locked:
                continue
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
                if not item.hidden and self._visible_in_crop(item):
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
        self._edge_item.set_edges(self._crop_rect, self._edges)  # edges are the export's edges
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
        self._edge_item.setVisible(False)  # the whole screenshot, plain, while cropping
        self._update_scene_rect()
        self.fit_rect(self._image_rect)  # show everything so cut areas can be brought back

    def apply_crop(self) -> None:
        if not self._mask.editing:
            return
        if self._drag is not None and self._drag["type"].startswith("crop"):
            self._drag = None  # Enter or a tool key mid-drag: keep the crop as it is now
        draft = QRectF(self._mask.crop)
        self._mask.set_editing(False)
        self._edge_item.setVisible(True)
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
        self._edge_item.setVisible(True)
        self._mask.set_crop(self._crop_rect)
        self._update_scene_rect()
        self.fit_image()

    def reset_crop_draft(self) -> None:
        """In crop mode: bring back the whole screenshot (kept when you apply)."""
        if self._mask.editing:
            self._mask.set_crop(self._image_rect)

    def _update_scene_rect(self) -> None:
        base = self._image_rect if self._mask.editing else self.frame_rect()
        room_x, room_y = self.pan_room()
        self._scene.setSceneRect(base.adjusted(-room_x, -room_y, room_x, room_y))

    def commit_text(self) -> None:
        editing = self.editing_text_item()
        if editing is not None:
            # Finish explicitly: scene.clearFocus() is a no-op when the window isn't active.
            editing.finish_editing()
            editing.clearFocus()

    # ------------------------------------------------------------------ document
    def to_document(self) -> dict:
        items = self.bottom_up(self.annotations())
        r = self._crop_rect
        states = [i.get_state() for i in items if not i.is_degenerate()]
        states += self._unparsed
        doc = {"version": document_version(states), "crop": [r.x(), r.y(), r.width(), r.height()], "items": states}
        if not self._geometry.is_original(self.original_image):
            doc["image"] = self._geometry.to_dict()  # items and crop are on the turned/resized image
        if not self._edges.is_plain():
            doc["edges"] = self._edges.to_dict()
        return doc

    def load_document(self, doc: dict) -> None:
        """Raises UnsupportedDocument, before changing anything, for a document whose markup this
        version would put in the wrong place. The caller opens the snip without it and doesn't
        autosave, so the file and its rendered image aren't overwritten."""
        version = doc.get("version", 1)
        if not isinstance(version, int) or version > DOCUMENT_VERSION:
            raise UnsupportedDocument(f"document version {version!r} is newer than this SnipForge")
        if "image" in doc:
            geometry = ImageGeometry.from_dict(doc["image"], self.original_image.size())
            if geometry is None:
                raise UnsupportedDocument(f"can't draw the snip as saved: {doc['image']!r}"[:200])
            self.set_image_geometry(geometry)
            self.set_crop_rect(QRectF(self._image_rect))  # until the saved crop says otherwise
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
        self._apply_edges(Edges.from_dict(doc.get("edges")))
        self.undo_stack.clear()

    def render_image(self, commit: bool = True, use_draft: bool = True) -> QImage:
        """Export the snip. While cropping, `use_draft` exports what's on screen; autosave passes
        False so the library always matches the saved document."""
        if commit:
            self.commit_text()
        draft = self._mask.editing and use_draft
        crop = self._mask.crop if draft else self._crop_rect
        hidden = not self._edge_item.isVisible()  # hidden while cropping, but they're part of the saved snip
        if hidden and not draft:
            self._edge_item.setVisible(True)
        try:
            return self._render_rect(crop.toAlignedRect())
        finally:
            if hidden and not draft:
                self._edge_item.setVisible(False)

    def shown_pixels(self, rect: QRect | None = None) -> QImage:
        """The snip under `rect` (all of it by default) as it looks with what hides parts of it
        (smart erase, blur, pixelate) and the pieces Smart Move moved, without other markup or
        edges: what Smart Move picks up and Edit text matches, so nothing hidden comes back through
        them. A null image if there isn't the memory for it."""
        whole = self._image_rect.toAlignedRect()
        rect = (rect or whole).intersected(whole)
        items = [i for i in self.annotations() if i.isVisible()]
        others = [i for i in items if not _shows_snip(i)]
        if len(others) == len(items) and rect == whole and self._edges.is_plain():
            return self.base_image  # nothing changes how it looks: the snip itself (shared, not copied)
        opacities = [(item, item.opacity()) for item in others]
        edges = self._edge_item.isVisible()
        for item, _ in opacities:
            item.setOpacity(0.0)  # rather than hidden: hiding an item deselects it
        self._edge_item.setVisible(False)
        try:
            return self._render_rect(rect)
        finally:
            for item, opacity in opacities:
                item.setOpacity(opacity)
            self._edge_item.setVisible(edges)

    def edit_line(self, rect: QRectF, old_text: str, new_text: str) -> None:
        """Edit text: smart-erase the line at `rect` and write `new_text` over it, matched in size
        and colour, as one undo step. The new text is an ordinary text box (select it to change it)."""
        from .edit_text import replacement, sample_area
        from .erase import EraseItem

        self.commit_text()
        area = sample_area(rect).toAlignedRect().intersected(self._image_rect.toAlignedRect())
        around = self.shown_pixels(area) if not area.isEmpty() else QImage()
        if around.isNull():
            self.notice.emit("Couldn't change that text: it isn't on the snip, or there isn't the memory for it.")
            return
        swap = replacement(around, QPointF(area.topLeft()), rect, old_text, self._image_rect)
        erase = EraseItem(self.tool_styles.get("erase", Style()), self.base_image)
        erase.set_scene_rect(swap.erase)
        erase.setZValue(self._next_z())
        if not new_text.strip():  # nothing typed: the line is just removed
            self.undo_stack.push(AddItemsCommand(self._scene, [erase], "Edit text"))
            self._select([erase])
            return
        text = self._adopt(TextItem(swap.style, new_text))
        text.setPos(swap.pos)
        text.setZValue(erase.zValue() + 1)  # over its erase (they'd tie: neither is in the scene yet)
        self.undo_stack.push(AddItemsCommand(self._scene, [erase, text], "Edit text"))
        self._select([text])

    def content_image(self) -> QImage:
        """The snip as it shows without markup (smart erases filled in, blurs blurred, moved pieces
        where they were moved): what Grab text, Redact and Edit text read, so text that's hidden
        isn't found again and moved text is found where it is now."""
        return self.shown_pixels()

    def _render_rect(self, rect) -> QImage:
        """The snip and its markup inside `rect` (scene pixels), without crop shading or handles."""
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

    # ------------------------------------------------------------------ helpers
    def _adopt(self, item):
        if hasattr(item, "on_edit_finished"):
            item.on_edit_finished = self._on_text_finished
        return item

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

    def _select(self, items) -> None:
        self._scene.clearSelection()
        for item in items:
            item.setSelected(True)

    def _next_z(self) -> float:
        zs = [i.zValue() for i in self.annotations() if i.zValue() > -100]
        return max(zs, default=0) + 1

    # ------------------------------------------------------------------ the mini toolbar
    def _palette_changed(self) -> None:
        self._mini.set_swatches(colors.palette())

    def mini_toolbar(self) -> MiniToolbar:
        return self._mini

    def refresh_mini_toolbar(self, *_args) -> None:
        """Show the mini toolbar over the selection, or hide it while there's nothing selected,
        a drag or crop is under way, or you're typing (it would cover the words)."""
        selected = [i for i in self.selected_annotations() if i.scene() is self._scene]
        busy = self._drag is not None or self.editing_text_item() is not None or self._mask.editing \
            or self.picking() or self.choosing() or self.closing
        if not selected or busy:
            self._mini.show_for(None, None, False)
            return
        bounds = QRectF()
        for item in selected:
            bounds = bounds.united(item.sceneBoundingRect()) if not bounds.isNull() else item.sceneBoundingRect()
        top_left = self.mapFromScene(bounds.topLeft())
        bottom_right = self.mapFromScene(bounds.bottomRight())
        kinds = {i.kind for i in selected}
        colorable = all("color" in STYLE_FIELDS.get(k, ()) for k in kinds)
        color = selected[0].style.color if colorable else None
        self._mini.show_for(QRect(top_left, bottom_right).normalized(), color, all(i.locked for i in selected))

    # ------------------------------------------------------------------ signals
    def _on_selection_changed(self) -> None:
        if self.closing:
            return
        self._overlay.set_selection(self.selected_annotations())
        self.selection_changed.emit()

    def _on_rubber_band(self, rect, _start: QPointF, _end: QPointF) -> None:
        """Qt's scene points trail the pointer by one move; its viewport rectangle is current."""
        band = None if rect.isNull() else QRectF(self.mapToScene(rect.topLeft()), self.mapToScene(rect.bottomRight()))
        self._overlay.set_band(band.normalized() if band is not None else None)

    def _on_undo_index(self, _index: int) -> None:
        self.stop_picking()  # the snip changed (undo, redo, a turn, a delete...): the eyedropper's picture is old
        self.stop_choosing()  # and Edit text's lines may not be where they were
        self._overlay.update()
        self.selection_changed.emit()
        self.document_changed.emit()
