"""Editable canvas: the snip plus annotation items, tools, crop, undo and export.

The canvas is split by concern: canvas_input.py handles the keyboard and mouse, canvas_draw.py what
those gestures draw, and canvas_view.py zoom, fit and the background.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QUndoStack
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView

from .. import theme
from .beautify import BeautifyStyle
from .canvas_draw import DrawMixin
from .canvas_image import ImageMixin
from .canvas_input import InputMixin
from .canvas_pick import PickMixin
from .canvas_view import MAX_ZOOM as MAX_ZOOM  # re-exported: the window's zoom buttons use these
from .canvas_view import MIN_ZOOM as MIN_ZOOM
from .canvas_view import SCENE_MARGIN, ViewMixin
from .commands import AddItemsCommand, CropCommand, RemoveItemsCommand, StateCommand
from .crop import MIN_CROP, CropMask
from .eyedropper import Loupe
from .factory import create_item
from .handles import HandleOverlay
from .image_ops import ImageGeometry
from .items import Annotation, EffectItem
from .style import DEFAULT_TOOL_STYLES, Style
from .text_items import EditableText

DUPLICATE_OFFSET = 16.0
DOCUMENT_VERSION = 1  # documents from newer versions are opened read-only (see load_document)


class UnsupportedDocument(ValueError):
    """Saved by a newer SnipForge, in a way this version can't show correctly."""


class Canvas(InputMixin, DrawMixin, PickMixin, ImageMixin, ViewMixin, QGraphicsView):
    tool_changed = Signal(str)
    selection_changed = Signal()
    zoom_changed = Signal(float)
    document_changed = Signal()
    picking_changed = Signal(bool)  # the eyedropper started or ended
    color_picked = Signal(str)  # "#rrggbb", now the selection's or the tool's color

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
        self._pick_source: QImage | None = None  # the snip as you see it, while the eyedropper is on
        self._just_picked = False
        self._loupe = Loupe(self.viewport())

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

    def style_target(self) -> tuple[str, Style | None]:
        """What the property bar should show: the selection, else the active tool."""
        selected = self.selected_annotations()
        if selected:
            return selected[0].kind, selected[0].style
        return self.tool, self.tool_styles.get(self.tool)

    # ------------------------------------------------------------------ tools and style
    def set_tool(self, tool: str) -> None:
        self.stop_picking()
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
        doc = {"version": 1, "crop": [r.x(), r.y(), r.width(), r.height()], "items": states + self._unparsed}
        if not self._geometry.is_original(self.original_image):
            doc["image"] = self._geometry.to_dict()  # items and crop are on the turned/resized image
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
        self.undo_stack.clear()

    def render_image(self, commit: bool = True, use_draft: bool = True) -> QImage:
        """Export the snip. While cropping, `use_draft` exports what's on screen; autosave passes
        False so the library always matches the saved document."""
        if commit:
            self.commit_text()
        crop = self._mask.crop if (self._mask.editing and use_draft) else self._crop_rect
        return self._render_rect(crop.toAlignedRect())

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

    # ------------------------------------------------------------------ signals
    def _on_selection_changed(self) -> None:
        if self.closing:
            return
        self._overlay.set_selection(self.selected_annotations())
        self.selection_changed.emit()

    def _on_undo_index(self, _index: int) -> None:
        self._overlay.update()
        self.selection_changed.emit()
        self.document_changed.emit()
