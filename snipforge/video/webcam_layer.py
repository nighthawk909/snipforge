"""The webcam in recordings: the camera's latest picture drawn into each frame where its bubble sits.

The bubble on screen is kept out of the capture (webcam_bubble.py), so DXGI's frames show what's
behind it; this layer paints the camera back in, in the bubble's shape, mirrored as the bubble shows
it. paint_camera() is shared with the bubble, so the recording looks exactly like the preview.

It runs on the frame thread (screen_frames.py) about 30 times a second, so it only reads two
lock-protected snapshots (the camera's picture and the bubble's place) and draws: no Qt widgets,
nothing slow.

CompositeOverlay lets the recorder use several overlays at once, the webcam under the pointer.
"""
from __future__ import annotations

import logging
from typing import Protocol

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QImage, QPainter, QPainterPath, QTransform

log = logging.getLogger(__name__)

CIRCLE, ROUNDED, RECTANGLE = "circle", "rounded", "rectangle"
SHAPES = (CIRCLE, ROUNDED, RECTANGLE)
ROUNDING = 0.12  # a rounded rectangle's corner radius, as a share of its shorter side
# The picture is scaled to overfill the shape by this many pixels each way, so smoothing at the
# shape's edge never samples past the picture's edge (a texture brush would wrap round to the far side).
BLEED = 1.0


class CameraPictures(Protocol):
    def latest(self) -> QImage | None: ...


class BubblePlace(Protocol):
    def snapshot(self): ...  # a webcam_bubble.BubbleSnapshot, or None while the bubble is hidden


def shape_path(rect: QRectF, shape: str) -> QPainterPath:
    """The outline of a bubble of `shape` filling `rect`."""
    path = QPainterPath()
    if shape == CIRCLE:
        path.addEllipse(rect)
    elif shape == ROUNDED:
        radius = min(rect.width(), rect.height()) * ROUNDING
        path.addRoundedRect(rect, radius, radius)
    else:
        path.addRect(rect)
    return path


def paint_camera(painter: QPainter, image: QImage, target: QRectF, shape: str) -> None:
    """Fills `shape` in `target` with `image`, mirrored like a mirror and cropped to fill (never
    squashed). The picture is a texture brush, so the shape's edge is smooth (a clip path isn't)."""
    if target.isEmpty() or image.isNull():
        return
    scale = max((target.width() + 2 * BLEED) / image.width(), (target.height() + 2 * BLEED) / image.height())
    centre = target.center()
    # Picture centre to target centre, flipped left to right (QTransform applies the last step first).
    placement = QTransform()
    placement.translate(centre.x(), centre.y())
    placement.scale(-scale, scale)
    placement.translate(-image.width() / 2, -image.height() / 2)
    brush = QBrush(image)
    brush.setTransform(placement)
    painter.save()
    try:
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setPen(Qt.NoPen)
        painter.setBrush(brush)
        painter.drawPath(shape_path(target, shape))
    finally:
        painter.restore()


class WebcamLayer:
    """Draws the camera into frames of `area` (x, y, width, height in physical desktop pixels) where
    `bubble` is. A frame may be smaller than the area (scaled down): the camera is scaled to match."""

    def __init__(self, area: tuple[int, int, int, int], camera: CameraPictures, bubble: BubblePlace) -> None:
        self.area = tuple(area)
        self._camera = camera
        self._bubble = bubble

    def prewarm(self, now: float | None = None) -> None:
        """Nothing to make ahead: here so the recorder can treat every overlay alike."""

    def draw(self, frame: QImage, now: float) -> None:
        """Draws into `frame` in place; does nothing while the bubble is hidden, outside the area, or
        the camera hasn't sent a picture yet."""
        snap = self._bubble.snapshot()
        if snap is None or not self._overlaps(snap.rect):
            return
        image = self._camera.latest()
        if image is None or image.isNull():
            return
        painter = QPainter(frame)
        try:
            _place(painter, frame, self.area)
            paint_camera(painter, image, QRectF(*snap.rect), snap.shape)
        finally:
            painter.end()

    def _overlaps(self, rect: tuple[int, int, int, int]) -> bool:
        x, y, width, height = self.area
        bx, by, bw, bh = rect
        return bx < x + width and by < y + height and bx + bw > x and by + bh > y


def _place(painter: QPainter, frame: QImage, area: tuple[int, int, int, int]) -> None:
    """Lets the painter work in desktop pixels: the area's top-left is the frame's (as cursor.py's
    PointerOverlay does, so both overlays agree on where things are)."""
    x, y, width, height = area
    ratio = frame.devicePixelRatio()
    scale_x, scale_y = frame.width() / ratio / width, frame.height() / ratio / height
    if (scale_x, scale_y) != (1.0, 1.0):
        painter.scale(scale_x, scale_y)
    painter.translate(-x, -y)


class CompositeOverlay:
    """Several overlays drawn into each frame in turn, the first given at the bottom: the recorder
    passes the webcam first, so the pointer is drawn over it.

    An overlay that fails is dropped and the others go on (a camera glitch mustn't take the pointer
    with it); `failures` lists what was dropped and why, for the recorder to warn about.
    """

    def __init__(self, *overlays) -> None:
        self._overlays = tuple(overlay for overlay in overlays if overlay is not None)
        self._failures: tuple[tuple[object, Exception], ...] = ()

    @property
    def overlays(self) -> tuple:
        return self._overlays

    @property
    def failures(self) -> tuple[tuple[object, Exception], ...]:
        return self._failures

    def prewarm(self, now: float | None = None) -> None:
        for overlay in self._overlays:
            prewarm = getattr(overlay, "prewarm", None)
            if prewarm is not None:
                prewarm(now)

    def draw(self, frame: QImage, now: float) -> None:
        for overlay in self._overlays:
            try:
                overlay.draw(frame, now)
            except Exception as exc:  # the recording matters more than any one thing drawn into it
                log.exception("Drawing %s failed; recording without it", type(overlay).__name__)
                # New tuples, not edits: the main thread may be reading these while this thread draws.
                self._overlays = tuple(o for o in self._overlays if o is not overlay)
                self._failures = (*self._failures, (overlay, exc))
