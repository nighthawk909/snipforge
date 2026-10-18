"""Rotate, flip and resize the canvas's image, with the crop and every annotation following along
(the geometry itself is in image_ops.py)."""
from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPixmap

from . import image_ops
from .canvas_view import SCENE_MARGIN
from .commands import ImageCommand
from .image_ops import ImageGeometry, Remap
from .items import EffectItem, SpotlightItem


class ImageMixin:
    """For the Canvas. Uses its `original_image`, `base_image`, `_geometry`, `_image_rect`,
    `_crop_rect`, `_base_item`, `_mask`, `_overlay` and `undo_stack`."""

    def image_geometry(self) -> ImageGeometry:
        return self._geometry

    def rotate_image(self, clockwise: bool = True) -> None:
        geometry, remap = image_ops.rotate(self._geometry, clockwise)
        self._change_image("Rotate right" if clockwise else "Rotate left", geometry, remap)

    def flip_image(self, horizontal: bool = True) -> None:
        geometry, remap = image_ops.flip(self._geometry, horizontal)
        self._change_image("Flip horizontal" if horizontal else "Flip vertical", geometry, remap)

    def can_resize(self, width: int, height: int) -> bool:
        """Whether resize_image(width, height) makes an image SnipForge will make."""
        if width < 1 or height < 1:
            return False
        return image_ops.fits(width, height) and image_ops.fits(*self._full_size(width, height))

    def _full_size(self, width: int, height: int) -> tuple[int, int]:
        """The whole image's size when the crop becomes `width` x `height`."""
        crop = self._crop_rect
        return (max(1, round(self._geometry.width * width / crop.width())),
                max(1, round(self._geometry.height * height / crop.height())))

    def resize_image(self, width: int, height: int) -> None:
        """Make the snip as you'd export it (the crop, if there is one) `width` x `height` pixels.
        Raises ValueError for a size SnipForge won't make."""
        self._finish_gestures()
        if not self.can_resize(width, height):
            raise ValueError(f"SnipForge doesn't make {width} x {height} images")
        crop = self._crop_rect
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
            if isinstance(item, EffectItem):
                item.set_base(self.base_image)
            elif isinstance(item, SpotlightItem):
                item.set_image_rect(self._image_rect)
        self._update_scene_rect()
        self._overlay.set_bounds(self._image_rect.adjusted(-SCENE_MARGIN, -SCENE_MARGIN, SCENE_MARGIN, SCENE_MARGIN))

    def _finish_gestures(self) -> None:
        """Typing, a drag or a crop in progress ends before the image changes under it."""
        self.commit_text()
        self._cancel_drag()
        if self.tool == "crop":
            self.set_tool("select")  # leaving Crop applies it

    def _change_image(self, text: str, geometry: ImageGeometry, remap: Remap, crop: QRectF | None = None) -> None:
        self._finish_gestures()
        if crop is None:
            bounds = QRectF(0, 0, geometry.width, geometry.height)
            crop = image_ops.snap(remap.transform.mapRect(self._crop_rect), bounds)
        changes = [(item, item.get_state(), image_ops.mapped_state(item, remap)) for item in self.annotations()]
        self.undo_stack.push(ImageCommand(self, text, (self._geometry, geometry), (self._crop_rect, crop), changes))
