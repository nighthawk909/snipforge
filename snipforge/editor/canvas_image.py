"""Rotate, flip and resize the canvas's image, with the crop and every annotation following along
(the geometry itself is in image_ops.py)."""
from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPixmap

from . import image_ops
from .canvas_view import SCENE_MARGIN
from .commands import EdgesCommand, ImageCommand
from .crop import MIN_CROP
from .cutout import MIN_LEFT
from .edges import Edges
from .image_ops import ImageGeometry, Remap
from .items import SpotlightItem

NEWER_MARKUP = "This snip has markup from a newer SnipForge, which this version can't turn or resize along with it."


class ImageMixin:
    """For the Canvas. Uses its `original_image`, `base_image`, `_geometry`, `_image_rect`,
    `_crop_rect`, `_base_item`, `_mask`, `_overlay` and `undo_stack`."""

    def image_geometry(self) -> ImageGeometry:
        return self._geometry

    def rotate_image(self, clockwise: bool = True) -> None:
        geometry, remap = image_ops.rotate(self._geometry, clockwise)
        self._change_image("Rotate right" if clockwise else "Rotate left", geometry, remap)

    def cut_out(self, axis: str, start: int, end: int, seam: str = "straight") -> None:
        """Take out columns (axis "x") or rows ("y") `start` to `end` and join the rest; at least
        a few pixels always stay. Raises ValueError for markup from a newer SnipForge."""
        length = self._geometry.size()[0 if axis == "x" else 1]
        start, end = max(0, start), min(end, length)
        end = min(end, start + length - MIN_LEFT)
        if end - start < 1:
            return
        geometry, remap = image_ops.cut(self._geometry, axis, start, end, seam)
        self._change_image("Cut out", geometry, remap)

    def edges(self) -> Edges:
        return self._edges

    def set_edges(self, edges: Edges) -> None:
        """A border and torn sides for the snip, as one undo step (dragging the width is one)."""
        if edges == self._edges:
            return
        old = self._edges
        width_only = bool(old.border and edges.border) and Edges(old.border, edges.color, edges.torn) == old
        key = ("edges", "width") if width_only else None
        self.undo_stack.push(EdgesCommand(self, old, edges, merge_key=key))

    def _apply_edges(self, edges: Edges) -> None:
        self._edges = edges
        self._edge_item.set_edges(self._crop_rect, edges)

    def flip_image(self, horizontal: bool = True) -> None:
        geometry, remap = image_ops.flip(self._geometry, horizontal)
        self._change_image("Flip horizontal" if horizontal else "Flip vertical", geometry, remap)

    def can_change_image(self) -> bool:
        """False while the snip has markup from a newer SnipForge: this version can't move it."""
        return not self._unparsed

    def can_resize(self, width: int, height: int) -> bool:
        """Whether resize_image(width, height) makes an image SnipForge will make."""
        if width < 1 or height < 1:
            return False
        original = self.original_image.size()
        if not (image_ops.fits(width, height, original) and image_ops.fits(*self._full_size(width, height), original)):
            return False
        geometry, _remap = image_ops.resize(self._geometry, *self._full_size(width, height))
        return image_ops.reopens(geometry, original)  # e.g. a cut snip squeezed to almost nothing

    def _full_size(self, width: int, height: int) -> tuple[int, int]:
        """The whole image's size when the crop becomes `width` x `height`."""
        crop = self._crop_rect
        w, h = self._geometry.size()
        return max(1, round(w * width / crop.width())), max(1, round(h * height / crop.height()))

    def resize_image(self, width: int, height: int) -> None:
        """Make the snip as you'd export it (the crop, if there is one) `width` x `height` pixels.
        Raises ValueError for a size SnipForge won't make."""
        if not self.can_change_image():
            raise ValueError(NEWER_MARKUP)
        self._finish_gestures()
        if not self.can_resize(width, height):
            raise ValueError(f"SnipForge doesn't make {width} x {height} images")
        crop = self._crop_rect
        if crop.size().toSize().toTuple() == (width, height):
            return  # already that size
        full_w, full_h = self._full_size(width, height)
        geometry, remap = image_ops.resize(self._geometry, full_w, full_h)
        left = min(round(crop.x() * remap.sx), full_w - width)
        top = min(round(crop.y() * remap.sy), full_h - height)
        self._change_image("Resize", geometry, remap, QRectF(max(0, left), max(0, top), width, height))

    def set_image_geometry(self, geometry: ImageGeometry) -> None:
        """Show the original drawn with `geometry` (annotations are the caller's to move)."""
        self._geometry = geometry
        self.base_image = image_ops.render(self.original_image, geometry)
        self._image_rect = QRectF(self.base_image.rect())
        self._base_item.setPixmap(QPixmap.fromImage(self.base_image))
        self._mask.set_image_rect(self._image_rect)
        for item in self.annotations():
            if hasattr(item, "set_base"):  # redactions and magnifiers draw from the snip's pixels
                item.set_base(self.base_image)
            elif isinstance(item, SpotlightItem):
                item.set_image_rect(self._image_rect)
        self._update_scene_rect()
        self._overlay.set_bounds(self._image_rect.adjusted(-SCENE_MARGIN, -SCENE_MARGIN, SCENE_MARGIN, SCENE_MARGIN))

    def _finish_gestures(self) -> None:
        """Typing, a drag, a crop, the eyedropper or Edit text in progress ends before the image changes under it."""
        self.stop_picking()
        self.stop_choosing()
        self.commit_text()
        self._cancel_drag()
        if self.tool == "crop":
            self.set_tool("select")  # leaving Crop applies it

    def _change_image(self, text: str, geometry: ImageGeometry, remap: Remap, crop: QRectF | None = None) -> None:
        if not self.can_change_image():
            raise ValueError(NEWER_MARKUP)
        if not image_ops.reopens(geometry, self.original_image.size()):
            raise ValueError("SnipForge can't save the snip that way")  # never save what won't open again
        self._finish_gestures()
        bounds = QRectF(0, 0, *geometry.size())
        if crop is None:
            crop = image_ops.snap(remap.map_rect(self._crop_rect), bounds)
        if remap.cut is not None and (crop.width() < MIN_CROP or crop.height() < MIN_CROP):
            crop = bounds  # the cut took away almost all of the crop
        edges = self._edges
        if remap.sides:  # a torn bottom is a torn left once turned clockwise
            edges = Edges(edges.border, edges.color, frozenset(remap.sides.get(s, s) for s in edges.torn))
        changes = [(item, item.get_state(), image_ops.mapped_state(item, remap)) for item in self.annotations()]
        self.undo_stack.push(ImageCommand(self, text, (self._geometry, geometry), (self._crop_rect, crop), changes,
                                          (self._edges, edges)))
