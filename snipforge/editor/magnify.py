"""Magnify: a lens that shows an enlarged copy of another part of the snip.

You drag over what to magnify (the source), and the lens appears beside it at twice the size. Both
move and resize: drag the lens to move it (the source stays put), drag inside the source's frame to
show another area, and use either one's corner handles to resize it. The lens keeps the source's
shape, so what it shows is never stretched. It's a circle, a box, a rounded box or a pill (the
panel's Shape), with a border in the annotation's color and width.

The lens shows the snip's pixels with its blurs and pixelations on top, so nothing hidden ever
shows up enlarged.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem

from .items import HIT_SLOP, BoxItem, make_pen, stroke_band
from .style import Style

DEFAULT_SOURCE = 80.0  # a click magnifies this much of the snip around it
ZOOMS = (2.0, 1.5, 1.25)  # a new lens is twice the size of what it shows, or less if that won't fit
MIN_SOURCE = 6.0
GAP = 16.0  # between the source and a new lens
SOURCE_HANDLES = ("s_tl", "s_tr", "s_br", "s_bl")
LENS_HANDLES = ("tl", "tr", "br", "bl")  # corners only: the lens keeps the source's shape


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


def _beside(source: QRectF, w: float, h: float) -> tuple[QRectF, ...]:
    return (QRectF(source.right() + GAP, source.center().y() - h / 2, w, h),
            QRectF(source.left() - GAP - w, source.center().y() - h / 2, w, h),
            QRectF(source.center().x() - w / 2, source.bottom() + GAP, w, h),
            QRectF(source.center().x() - w / 2, source.top() - GAP - h, w, h))


def place_lens(source: QRectF, bounds: QRectF) -> QRectF:
    """Where a new lens goes, inside `bounds` (what you can see of the snip): beside the source
    (right, left, below, above), at the biggest zoom in ZOOMS that fits clear of it; failing that,
    slid onto the snip; failing that, the middle."""
    for zoom in ZOOMS:
        w, h = source.width() * zoom, source.height() * zoom
        for rect in _beside(source, w, h):
            if bounds.contains(rect):
                return rect
        for rect in _beside(source, w, h):
            moved = QRectF(rect)
            moved.moveLeft(min(max(moved.left(), bounds.left()), bounds.right() - w))
            moved.moveTop(min(max(moved.top(), bounds.top()), bounds.bottom() - h))
            if bounds.contains(moved) and not moved.intersects(source):
                return moved
    w, h = source.width() * ZOOMS[0], source.height() * ZOOMS[0]
    middle = QRectF(0, 0, w, h)
    middle.moveCenter(bounds.center())
    return middle


def default_source(point: QPointF, bounds: QRectF) -> QRectF:
    """What a click magnifies: DEFAULT_SOURCE square around it, kept on the snip."""
    side = min(DEFAULT_SOURCE, bounds.width(), bounds.height())
    rect = QRectF(0, 0, side, side)
    rect.moveCenter(point)
    return _kept_inside(rect, bounds)


def _kept_inside(rect: QRectF, bounds: QRectF) -> QRectF:
    moved = QRectF(rect)
    moved.moveLeft(min(max(moved.left(), bounds.left()), bounds.right() - moved.width()))
    moved.moveTop(min(max(moved.top(), bounds.top()), bounds.bottom() - moved.height()))
    return moved


class MagnifyItem(BoxItem):
    """The box is the lens. The source is kept in scene coordinates, so moving the lens (dragging
    it, nudging it, duplicating it) leaves the source where it is."""

    kind = "magnify"

    def __init__(self, style: Style, base: QImage) -> None:
        self._base = base
        self._source = QRectF()
        self._source_origin = QRectF()
        self._grab: QPointF | None = None  # where a drag inside the source started
        self._picture: tuple[tuple, QImage] | None = None  # (key, the source as the snip shows it)
        super().__init__(style)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges)

    def itemChange(self, change, value):  # noqa: N802 - Qt API
        if change == QGraphicsItem.ItemPositionHasChanged:
            # The frame and the line to it are drawn relative to the lens, and a shadow keeps a
            # picture of the item: both must be redrawn when the lens moves.
            self.update()
        return super().itemChange(change, value)

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

    def _fit_lens_to_source(self) -> None:
        """Keep the lens the source's shape (same width) so what it shows isn't stretched."""
        if self._source.width() > 0 and not self._rect.isEmpty():
            self.prepareGeometryChange()
            self._rect.setHeight(self._rect.width() * self._source.height() / self._source.width())
            self.update()

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
    def _handles(self) -> dict[str, QPointF]:
        lens = {name: point for name, point in super()._handles().items() if name in LENS_HANDLES}
        s = self._source
        return {**lens, "s_tl": s.topLeft(), "s_tr": s.topRight(), "s_br": s.bottomRight(), "s_bl": s.bottomLeft()}

    def area_handle_at(self, scene_pos: QPointF) -> str | None:
        """A press on or inside the source's frame moves what the lens shows (unless the lens,
        which is drawn on top, is there)."""
        if self.scene_rect().contains(scene_pos):
            return None
        grab = self._source.adjusted(-HIT_SLOP, -HIT_SLOP, HIT_SLOP, HIT_SLOP)
        return "source" if grab.contains(scene_pos) else None

    def press_at(self, scene_pos: QPointF) -> None:
        """Where a handle drag started, so dragging the source moves it by exactly as much."""
        self._grab = QPointF(scene_pos)

    def selection_rects(self) -> list[QRectF]:
        """The overlay outlines the lens and the source, not one box around both."""
        return [self.scene_rect(), self.source()]

    def begin_handle_drag(self) -> None:
        super().begin_handle_drag()
        self._source_origin = QRectF(self._source)
        self._grab = None

    def drag_handle(self, name: str, scene_pos: QPointF, shift: bool) -> None:
        bounds = QRectF(self._base.rect())
        if name == "source":
            if self._grab is None:
                self._grab = QPointF(scene_pos)
            moved = self._source_origin.translated(scene_pos - self._grab)
            self.set_source(_kept_inside(moved, bounds) if bounds.contains(self._source_origin) else moved)
        elif name in SOURCE_HANDLES:
            self.set_source(_resized(self._source_origin, name[2:], scene_pos, shift).intersected(bounds)
                            if bounds.intersects(self._source_origin) else
                            _resized(self._source_origin, name[2:], scene_pos, shift))
            self._fit_lens_to_source()
        else:
            self._drag_lens_corner(name, scene_pos)

    def _drag_lens_corner(self, name: str, pos: QPointF) -> None:
        """Resize the lens from the opposite corner, keeping the source's shape."""
        r = self._drag_origin if self._drag_origin is not None else self.scene_rect()
        anchor = {"br": r.topLeft(), "tl": r.bottomRight(), "tr": r.bottomLeft(), "bl": r.topRight()}[name]
        ratio = self._source.height() / self._source.width() if self._source.width() > 0 else 1.0
        dx, dy = pos.x() - anchor.x(), pos.y() - anchor.y()
        w = max(abs(dx), abs(dy) / ratio, MIN_SOURCE)
        h = w * ratio
        corner = QPointF(anchor.x() + (w if dx >= 0 else -w), anchor.y() + (h if dy >= 0 else -h))
        self.set_scene_rect(QRectF(anchor, corner).normalized())

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
        frame = (Qt.DotLine if self.style.dotted else Qt.DashLine) if self.style.dash else Qt.SolidLine
        p.setPen(QPen(color, max(1.0, self.style.width / 2), frame))
        p.setBrush(Qt.NoBrush)
        p.drawRect(source)
        if self._rect.isEmpty() or self._source.isEmpty():
            return
        p.save()
        p.setClipPath(lens, Qt.IntersectClip)
        p.fillRect(self._rect, QColor("#ffffff"))  # past the snip's edge
        picture, inside = self._source_picture()
        if not picture.isNull():
            p.drawImage(self._rect, picture, inside)
        p.restore()
        p.setPen(make_pen(color, max(1.0, self.style.width), self.style.dash, self.style.dotted))
        p.setBrush(Qt.NoBrush)
        p.drawPath(lens)

    def _source_picture(self) -> tuple[QImage, QRectF]:
        """The source as the snip shows it, at its own size: the snip's pixels with its blurs,
        pixelations and smart erases drawn in. Enlarging that finished picture (not the snip with the
        redactions enlarged on top) keeps what a redaction hides from blending in at its edges.
        Returns the picture and where the source is in it."""
        area = self._source.toAlignedRect().adjusted(-1, -1, 1, 1).intersected(self._base.rect())
        if area.isEmpty():
            return QImage(), QRectF()
        every = self._redactions()
        hiding = [i for i in every if i.sceneBoundingRect().intersects(self._source)]
        # Every redaction is in the key, not just those over the source: a blur draws from the erases
        # under it, which may lie just outside. There are only ever a few.
        key = (area.getRect(), self._base.cacheKey(),
               tuple((id(i), i.sceneBoundingRect().getRect(), i.zValue(), i.kind, i.style.width, i.style.rounded)
                     for i in every))
        if self._picture is None or self._picture[0] != key:
            picture = self._base.copy(area).convertToFormat(QImage.Format_ARGB32_Premultiplied)
            painter = QPainter(picture)
            painter.translate(-area.x(), -area.y())
            for item in hiding:
                painter.save()
                painter.translate(item.pos())
                item.paint(painter, None, None)
                painter.restore()
            painter.end()
            self._picture = (key, picture)
        return self._picture[1], self._source.translated(-area.x(), -area.y())

    def _redactions(self) -> list:
        """Every blur, pixelation and smart erase in the snip, bottom first."""
        scene = self.scene()
        if scene is None:
            return []
        # scene.items() and not items(rect): the rect query comes back empty while the scene paints.
        return [item for item in sorted(scene.items(), key=lambda i: i.zValue())
                if getattr(item, "hides_content", False) and item.isVisible()]

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
