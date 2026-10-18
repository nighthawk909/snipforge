"""Magnify: a lens that shows an enlarged copy of another part of the snip.

You drag over what to magnify (the source), and the lens appears beside it at twice the size. Both
move and resize: drag the lens to move it (the source stays put), drag inside the source's frame to
show another area, and use either one's corner handles to resize it. The lens is a circle, a box,
a rounded box or a pill (the panel's Shape), with a border in the annotation's color and width.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

from .items import HIT_SLOP, BoxItem, make_pen, stroke_band
from .style import Style

DEFAULT_SOURCE = 80.0  # a click magnifies this much of the snip around it
ZOOM = 2.0  # a new lens is twice the size of what it shows
MIN_SOURCE = 6.0
GAP = 16.0  # between the source and a new lens
SOURCE_HANDLES = ("s_tl", "s_tr", "s_br", "s_bl")


def lens_path(rect: QRectF, shape: str) -> QPainterPath:
    path = QPainterPath()
    if shape == "ellipse":
        path.addEllipse(rect)
    elif shape == "pill":
        radius = min(rect.width(), rect.height()) / 2
        path.addRoundedRect(rect, radius, radius)
    elif shape == "rect":
        path.addRect(rect)
    else:
        path.addRoundedRect(rect, 10, 10)
    return path


def place_lens(source: QRectF, bounds: QRectF, zoom: float = ZOOM) -> QRectF:
    """Where a new lens goes: right of the source if it fits on the image, else left, below or
    above; failing that, slid onto the image clear of the source; failing that, the middle."""
    w, h = source.width() * zoom, source.height() * zoom
    beside = (QRectF(source.right() + GAP, source.center().y() - h / 2, w, h),
              QRectF(source.left() - GAP - w, source.center().y() - h / 2, w, h),
              QRectF(source.center().x() - w / 2, source.bottom() + GAP, w, h),
              QRectF(source.center().x() - w / 2, source.top() - GAP - h, w, h))
    for rect in beside:
        if bounds.contains(rect):
            return rect
    for rect in beside:
        moved = QRectF(rect)
        moved.moveLeft(min(max(moved.left(), bounds.left()), bounds.right() - w))
        moved.moveTop(min(max(moved.top(), bounds.top()), bounds.bottom() - h))
        if bounds.contains(moved) and not moved.intersects(source):
            return moved
    middle = QRectF(0, 0, w, h)
    middle.moveCenter(bounds.center())
    return middle


def default_source(point: QPointF, bounds: QRectF) -> QRectF:
    """What a click magnifies: DEFAULT_SOURCE square around it, kept on the image."""
    side = min(DEFAULT_SOURCE, bounds.width(), bounds.height())
    rect = QRectF(0, 0, side, side)
    rect.moveCenter(point)
    rect.moveLeft(min(max(rect.left(), bounds.left()), bounds.right() - side))
    rect.moveTop(min(max(rect.top(), bounds.top()), bounds.bottom() - side))
    return rect


class MagnifyItem(BoxItem):
    """The box is the lens. The source is kept in scene coordinates, so moving the lens (dragging
    it, nudging it, duplicating it) leaves the source where it is."""

    kind = "magnify"

    def __init__(self, style: Style, base: QImage) -> None:
        self._base = base
        self._source = QRectF()
        self._source_origin = QRectF()
        self._grab: QPointF | None = None  # where a drag inside the source started
        super().__init__(style)

    # -- what it magnifies -------------------------------------------------------------
    def set_base(self, base: QImage) -> None:
        """The snip was turned or resized: enlarge the new pixels."""
        self._base = base
        self.update()

    def source(self) -> QRectF:
        return QRectF(self._source)

    def set_source(self, rect: QRectF) -> None:
        self.prepareGeometryChange()
        self._source = QRectF(rect).normalized()
        self.update()

    def zoom(self) -> float:
        if self._source.isEmpty():
            return 1.0
        return min(self._rect.width() / self._source.width(), self._rect.height() / self._source.height())

    # -- state ---------------------------------------------------------------------------
    def _geometry(self) -> dict:
        s = self._source
        return {"size": [self._rect.width(), self._rect.height()], "source": [s.x(), s.y(), s.width(), s.height()]}

    def _set_geometry(self, state: dict) -> None:
        self._rect = QRectF(0, 0, *state["size"])
        self._source = QRectF(*state["source"]).normalized()

    def is_degenerate(self) -> bool:
        return super().is_degenerate() or self._source.width() < 2 or self._source.height() < 2

    # -- handles ---------------------------------------------------------------------------
    def handle_points(self) -> dict[str, QPointF]:
        points = super().handle_points()
        s = self._source
        points.update({"s_tl": s.topLeft(), "s_tr": s.topRight(), "s_br": s.bottomRight(), "s_bl": s.bottomLeft()})
        return points

    def area_handle_at(self, scene_pos: QPointF) -> str | None:
        """A press inside the source's frame moves what the lens shows."""
        return "source" if self._source.contains(scene_pos) else None

    def selection_rects(self) -> list[QRectF]:
        """The overlay outlines the lens and the source, not one box around both."""
        return [self.scene_rect(), self.source()]

    def begin_handle_drag(self) -> None:
        super().begin_handle_drag()
        self._source_origin = QRectF(self._source)
        self._grab = None

    def press_at(self, scene_pos: QPointF) -> None:
        """Where a handle drag started, so dragging the source moves it by exactly as much."""
        self._grab = QPointF(scene_pos)

    def drag_handle(self, name: str, scene_pos: QPointF, shift: bool) -> None:
        if name == "source":
            if self._grab is None:
                self._grab = QPointF(scene_pos)
            self.set_source(self._source_origin.translated(scene_pos - self._grab))
        elif name in SOURCE_HANDLES:
            self.set_source(_resized(self._source_origin, name[2:], scene_pos, shift))
        else:
            super().drag_handle(name, scene_pos, shift)

    # -- shape and painting ------------------------------------------------------------------
    def _local_source(self) -> QRectF:
        return self._source.translated(-self.pos())

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt API
        m = self.style.width + HIT_SLOP + 4
        return self._rect.united(self._local_source()).adjusted(-m, -m, m, m)

    def shape(self) -> QPainterPath:
        frame = QPainterPath()
        frame.addRect(self._local_source())
        return lens_path(self._rect, self.style.shape).united(stroke_band(frame, HIT_SLOP * 2))

    def paint(self, p: QPainter, _opt, _widget=None) -> None:
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        color = QColor(self.style.color)
        source = self._local_source()
        lens = lens_path(self._rect, self.style.shape)
        self._paint_connector(p, source, lens, color)
        p.setPen(QPen(color, max(1.0, self.style.width / 2), Qt.DashLine if self.style.dash else Qt.SolidLine))
        p.setBrush(Qt.NoBrush)
        p.drawRect(source)
        if not self._rect.isEmpty():
            p.save()
            p.setClipPath(lens, Qt.IntersectClip)
            p.fillRect(self._rect, QColor("#ffffff"))  # past the snip's edge
            p.drawImage(self._rect, self._base, self._source)
            p.restore()
            p.setPen(make_pen(color, max(1.0, self.style.width), self.style.dash))
            p.drawPath(lens)

    def _paint_connector(self, p: QPainter, source: QRectF, lens: QPainterPath, color: QColor) -> None:
        """A faint line from the source to the lens, so it's clear which area is enlarged."""
        if self._rect.isEmpty() or self._rect.intersects(source):
            return
        outside = QPainterPath()
        outside.addRect(self.boundingRect())
        frame = QPainterPath()
        frame.addRect(source)
        p.save()
        p.setClipPath(outside.subtracted(frame).subtracted(lens), Qt.IntersectClip)
        faint = QColor(color)
        faint.setAlpha(160)
        p.setPen(QPen(faint, 1.5, Qt.DashLine))
        p.drawLine(source.center(), self._rect.center())
        p.restore()


def _resized(origin: QRectF, corner: str, pos: QPointF, square: bool) -> QRectF:
    """The source with `corner` ("tl", "tr", "br" or "bl") dragged to `pos`, never smaller than
    MIN_SOURCE; Shift keeps it square."""
    left, top, right, bottom = origin.left(), origin.top(), origin.right(), origin.bottom()
    if "l" in corner:
        left = min(pos.x(), right - MIN_SOURCE)
    else:
        right = max(pos.x(), left + MIN_SOURCE)
    if "t" in corner:
        top = min(pos.y(), bottom - MIN_SOURCE)
    else:
        bottom = max(pos.y(), top + MIN_SOURCE)
    if square:
        side = max(right - left, bottom - top)
        if "l" in corner:
            left = right - side
        else:
            right = left + side
        if "t" in corner:
            top = bottom - side
        else:
            bottom = top + side
    return QRectF(QPointF(left, top), QPointF(right, bottom))
