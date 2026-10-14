"""Non-destructive crop.

While the Crop tool is active the whole screenshot is shown, the area being cut
is dimmed, and the crop edges have handles: drag them in to cut, drag them back
out to restore. Once applied, everything outside the crop is hidden (not just
dimmed) and exports use only the kept area. The original pixels are never
discarded, so re-entering Crop can always bring them back.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem

HANDLE_PX = 10
MIN_CROP = 8.0
HIDE_MARGIN = 5000.0  # how far past the image the "cut" fill extends (covers stray annotations)
ACCENT = QColor("#3b82f6")


# ---------------------------------------------------------------------------
# Geometry (pure functions)
# ---------------------------------------------------------------------------
def crop_handle_points(rect: QRectF) -> dict[str, QPointF]:
    cx, cy = rect.center().x(), rect.center().y()
    return {"tl": rect.topLeft(), "t": QPointF(cx, rect.top()), "tr": rect.topRight(),
            "r": QPointF(rect.right(), cy), "br": rect.bottomRight(), "b": QPointF(cx, rect.bottom()),
            "bl": rect.bottomLeft(), "l": QPointF(rect.left(), cy)}


def crop_handle_at(rect: QRectF, pos: QPointF, zoom: float) -> str | None:
    reach = (HANDLE_PX / 2 + 4) / (zoom or 1.0)
    for name, pt in crop_handle_points(rect).items():
        if abs(pt.x() - pos.x()) <= reach and abs(pt.y() - pos.y()) <= reach:
            return name
    return None


def snap(rect: QRectF) -> QRectF:
    """Crops are whole pixels, so the export is exactly what's shown."""
    left, top = round(rect.left()), round(rect.top())
    return QRectF(left, top, round(rect.right()) - left, round(rect.bottom()) - top)


def resize_crop(origin: QRectF, handle: str, pos: QPointF, bounds: QRectF) -> QRectF:
    """Move the dragged edge(s) to `pos`, staying inside the image and at least MIN_CROP big."""
    left, top, right, bottom = origin.left(), origin.top(), origin.right(), origin.bottom()
    x = min(max(pos.x(), bounds.left()), bounds.right())
    y = min(max(pos.y(), bounds.top()), bounds.bottom())
    if "l" in handle:
        left = min(x, right - MIN_CROP)
    if "r" in handle:
        right = max(x, left + MIN_CROP)
    if "t" in handle:
        top = min(y, bottom - MIN_CROP)
    if "b" in handle:
        bottom = max(y, top + MIN_CROP)
    return snap(QRectF(QPointF(left, top), QPointF(right, bottom)))


def move_crop(origin: QRectF, delta: QPointF, bounds: QRectF) -> QRectF:
    moved = origin.translated(delta)
    dx = max(bounds.left() - moved.left(), 0) - max(moved.right() - bounds.right(), 0)
    dy = max(bounds.top() - moved.top(), 0) - max(moved.bottom() - bounds.bottom(), 0)
    return snap(moved.translated(dx, dy))


def new_crop(origin: QPointF, pos: QPointF, bounds: QRectF) -> QRectF:
    return snap(QRectF(origin, pos).normalized().intersected(bounds))


# ---------------------------------------------------------------------------
# Mask item
# ---------------------------------------------------------------------------
class CropMask(QGraphicsItem):
    """Above all annotations: dims while editing, hides the cut area once applied.
    Never exported (the canvas hides it while rendering)."""

    def __init__(self, image_rect: QRectF, background: QColor) -> None:
        super().__init__()
        self._image = QRectF(image_rect)
        self._background = QColor(background)
        self.crop = QRectF(image_rect)
        self.editing = False
        self.setZValue(1000)
        self.setAcceptedMouseButtons(Qt.NoButton)

    def set_crop(self, rect: QRectF) -> None:
        self.crop = QRectF(rect)
        self.update()

    def set_editing(self, editing: bool) -> None:
        self.editing = editing
        self.update()

    def boundingRect(self) -> QRectF:
        return self._image.adjusted(-HIDE_MARGIN, -HIDE_MARGIN, HIDE_MARGIN, HIDE_MARGIN)

    def paint(self, p: QPainter, _opt, _widget=None) -> None:
        if self.editing:
            self._paint_editing(p, p.worldTransform().m11() or 1.0)
        elif self.crop != self._image:
            self._paint_cut(p)

    def _outside(self, outer: QRectF) -> QPainterPath:
        path = QPainterPath()
        path.addRect(outer)
        hole = QPainterPath()
        hole.addRect(self.crop)
        return path.subtracted(hole)

    def _paint_cut(self, p: QPainter) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(self._background)
        p.drawPath(self._outside(self.boundingRect()))
        for grow, alpha in ((6, 30), (3, 50), (1, 90)):  # soft edge so the kept area reads as a page
            ring = QPainterPath()
            ring.addRect(self.crop.adjusted(-grow, -grow + 2, grow, grow + 2))
            hole = QPainterPath()
            hole.addRect(self.crop)
            p.setBrush(QColor(0, 0, 0, alpha))
            p.drawPath(ring.subtracted(hole))

    def _paint_editing(self, p: QPainter, scale: float) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 160))
        p.drawPath(self._outside(self._image))
        r = self.crop
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 110), 1.0 / scale))
        for i in (1, 2):  # rule-of-thirds guides
            x = r.left() + r.width() * i / 3
            y = r.top() + r.height() * i / 3
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        p.setPen(QPen(QColor("#ffffff"), 1.5 / scale))
        p.drawRect(r)
        size = HANDLE_PX / scale
        p.setPen(QPen(ACCENT, 1.5 / scale))
        p.setBrush(QColor("#ffffff"))
        for pt in crop_handle_points(r).values():
            p.drawRect(QRectF(pt.x() - size / 2, pt.y() - size / 2, size, size))
        self._paint_size_label(p, scale)

    def _paint_size_label(self, p: QPainter, scale: float) -> None:
        text = f"{round(self.crop.width())} × {round(self.crop.height())}"
        p.save()
        p.translate(self.crop.topLeft())
        p.scale(1 / scale, 1 / scale)  # constant on-screen size at any zoom
        font = QFont("Segoe UI", 9)
        p.setFont(font)
        w = p.fontMetrics().horizontalAdvance(text) + 14
        h = p.fontMetrics().height() + 8
        box = QRectF(0, -h - 6, w, h) if self.crop.top() * scale > h + 8 else QRectF(6, 6, w, h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 20, 20, 230))
        p.drawRoundedRect(box, 5, 5)
        p.setPen(QColor("#ffffff"))
        p.drawText(box, Qt.AlignCenter, text)
        p.restore()
