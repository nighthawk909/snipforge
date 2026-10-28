"""Shape annotation items.

Every annotation can serialise itself to a plain dict (`get_state`) and
restore from one (`set_state`). Undo, duplicate and the library all build on
that, so editing never has to know an item's internals. Text and callouts live
in text_items.py, stamps in stamps.py, and `factory.create_item` builds any kind.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QGraphicsItem

from ..fonts import ui_font
from .style import USUAL_CORNER, USUAL_DIM, USUAL_OPACITY, Style, fill_opacity, fill_qcolor

FLAGS = QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable
HIT_SLOP = 8.0


def make_pen(color: QColor, width: float, dash: bool = False, dotted: bool = False) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    if dash and dotted:
        pen.setDashPattern([0.01, 2.0])  # in units of pen width; round caps turn each into a dot
    elif dash:
        pen.setDashPattern([2.2, 2.0])
    return pen


def style_pen(style: Style) -> QPen:
    """The outline pen for `style`: its color and line (solid, dashed or dotted)."""
    return make_pen(QColor(style.color), style.width, style.dash, style.dotted)


def constrain_45(origin: QPointF, point: QPointF) -> QPointF:
    """Snap `point` to the nearest 45-degree angle around `origin`."""
    dx, dy = point.x() - origin.x(), point.y() - origin.y()
    length = math.hypot(dx, dy)
    angle = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
    return QPointF(origin.x() + length * math.cos(angle), origin.y() + length * math.sin(angle))


def readable_on(color: QColor) -> QColor:
    luminance = 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()
    return QColor("#1c1c1e") if luminance > 170 else QColor("#ffffff")


DARK_INK, LIGHT_INK = "#1c1c1e", "#ffffff"
MIN_CONTRAST = 3.0  # WCAG's floor for large text: callout and text-box lettering is big and bold


def _luminance(color: QColor) -> float:
    def channel(value: int) -> float:
        v = value / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(color.red()) + 0.7152 * channel(color.green()) + 0.0722 * channel(color.blue())


def contrast(a: QColor, b: QColor) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def over_white(color: QColor) -> QColor:
    """A see-through color as it looks on a light snip, which most screenshots are."""
    alpha = color.alphaF()
    return QColor(*(round(v * alpha + 255 * (1 - alpha)) for v in (color.red(), color.green(), color.blue())))


def ink_on(background: QColor, preferred: QColor | None = None) -> QColor:
    """Text color for `background`: `preferred` if it reads there, else dark or white, whichever reads better."""
    shown = over_white(background)
    if preferred is not None and contrast(preferred, shown) >= MIN_CONTRAST:
        return QColor(preferred)
    dark, light = QColor(DARK_INK), QColor(LIGHT_INK)
    return dark if contrast(dark, shown) >= contrast(light, shown) else light


def fill_ink(kind: str, style: Style) -> QColor:
    """The letters' color on a filled text box or callout. The usual look (no fill color or opacity of
    its own) is as it always was. Otherwise the letters read on the fill: a text box keeps its color
    when it reads on a background of its own, and a callout its outline's when its fill is faint."""
    color = QColor(style.color)
    if not style.fill_color and style.fill_opacity == USUAL_OPACITY:
        return readable_on(color)
    if kind == "text":
        preferred = color if style.fill_color else None
    else:
        preferred = color if fill_opacity(kind, style) < 50 else None
    return ink_on(fill_qcolor(kind, style), preferred)


def stroke_band(path: QPainterPath, width: float) -> QPainterPath:
    stroker = QPainterPathStroker()
    stroker.setWidth(width)
    stroker.setCapStyle(Qt.RoundCap)
    stroker.setJoinStyle(Qt.RoundJoin)
    return stroker.createStroke(path)


class Annotation:
    """Mixin shared by all annotation items."""

    kind = ""
    supports_shadow = True

    def init_annotation(self, style: Style) -> None:
        self.style = style
        self.setFlags(FLAGS)
        self._apply_shadow()

    # -- state -------------------------------------------------------------
    def get_state(self) -> dict:
        state = {"kind": self.kind, "pos": [self.pos().x(), self.pos().y()],
                 "z": self.zValue(), "style": self.style.to_dict()}
        state.update(self._geometry())
        return state

    def set_state(self, state: dict) -> None:
        self.prepareGeometryChange()
        self.setPos(QPointF(*state["pos"]))
        self.setZValue(state.get("z", 0))
        # Style before geometry: layout that depends on style (callout text) must not resize the
        # restored geometry using the style being replaced.
        self.set_style(Style.from_dict(state["style"]))
        self._set_geometry(state)

    def _geometry(self) -> dict:
        return {}

    def _set_geometry(self, state: dict) -> None:
        pass

    # -- style -------------------------------------------------------------
    def set_style(self, style: Style) -> None:
        self.prepareGeometryChange()
        self.style = style
        self._apply_shadow()
        self.update()

    def _apply_shadow(self) -> None:
        if self.supports_shadow and self.style.shadow:
            effect = QGraphicsDropShadowEffect()
            effect.setBlurRadius(12)
            effect.setOffset(2, 3)
            effect.setColor(QColor(0, 0, 0, 120))
            self.setGraphicsEffect(effect)
        else:
            self.setGraphicsEffect(None)

    # -- handles -----------------------------------------------------------
    def handle_points(self) -> dict[str, QPointF]:
        """Handle name -> position in scene coordinates."""
        return {}

    def begin_handle_drag(self) -> None:
        pass

    def drag_handle(self, name: str, scene_pos: QPointF, shift: bool) -> None:
        pass

    def is_degenerate(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# Box-shaped items
# ---------------------------------------------------------------------------
class BoxItem(Annotation, QGraphicsItem):
    keep_square_with_shift = True
    always_square = False
    min_size = (6.0, 6.0)  # resizing by handle never goes below this (so nothing gets dropped on save)

    def __init__(self, style: Style) -> None:
        QGraphicsItem.__init__(self)
        self._rect = QRectF()
        self._drag_origin: QRectF | None = None
        self.init_annotation(style)

    def rect(self) -> QRectF:
        return QRectF(self._rect)

    def set_scene_rect(self, rect: QRectF) -> None:
        self.prepareGeometryChange()
        rect = rect.normalized()
        self.setPos(rect.topLeft())
        self._rect = QRectF(0, 0, rect.width(), rect.height())
        self._geometry_changed()
        self.update()

    def _geometry_changed(self) -> None:
        pass

    def scene_rect(self) -> QRectF:
        return self._rect.translated(self.pos())

    def _geometry(self) -> dict:
        return {"size": [self._rect.width(), self._rect.height()]}

    def _set_geometry(self, state: dict) -> None:
        self._rect = QRectF(0, 0, *state["size"])
        self._geometry_changed()

    def _margin(self) -> float:
        return self.style.width / 2 + 2

    def boundingRect(self) -> QRectF:
        m = self._margin()
        return self._rect.adjusted(-m, -m, m, m)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        m = self._margin()
        path.addRect(self._rect.adjusted(-m, -m, m, m))
        return path

    def is_degenerate(self) -> bool:
        return self._rect.width() < 4 or self._rect.height() < 4

    def handle_points(self) -> dict[str, QPointF]:
        r = self.scene_rect()
        cx, cy = r.center().x(), r.center().y()
        points = {"tl": r.topLeft(), "tr": r.topRight(), "br": r.bottomRight(), "bl": r.bottomLeft()}
        if not self.always_square:
            points.update({"t": QPointF(cx, r.top()), "r": QPointF(r.right(), cy),
                           "b": QPointF(cx, r.bottom()), "l": QPointF(r.left(), cy)})
        return points

    def begin_handle_drag(self) -> None:
        self._drag_origin = self.scene_rect()

    def drag_handle(self, name: str, scene_pos: QPointF, shift: bool) -> None:
        # Measure from the rect at drag start so dragging past the opposite edge flips cleanly.
        r = self._drag_origin if self._drag_origin is not None else self.scene_rect()
        left, top, right, bottom = r.left(), r.top(), r.right(), r.bottom()
        if "l" in name:
            left = scene_pos.x()
        if "r" in name:
            right = scene_pos.x()
        if "t" in name:
            top = scene_pos.y()
        if "b" in name:
            bottom = scene_pos.y()
        square = self.always_square or (shift and self.keep_square_with_shift)
        if square and len(name) == 2:
            side = max(abs(right - left), abs(bottom - top))
            if "l" in name:
                left = right - side if left < right else right + side
            else:
                right = left + side if right > left else left - side
            if "t" in name:
                top = bottom - side if top < bottom else bottom + side
            else:
                bottom = top + side if bottom > top else top - side
        rect = QRectF(QPointF(left, top), QPointF(right, bottom)).normalized()
        min_w, min_h = self.min_size
        if self.always_square:
            min_w = min_h = max(min_w, min_h)
        if rect.width() < min_w:  # grow away from the handle being dragged
            if "l" in name:
                rect.setLeft(rect.right() - min_w)
            else:
                rect.setWidth(min_w)
        if rect.height() < min_h:
            if "t" in name:
                rect.setTop(rect.bottom() - min_h)
            else:
                rect.setHeight(min_h)
        self.set_scene_rect(rect)


class _OutlineShape(BoxItem):
    """Rectangle/ellipse: hollow ones are only clickable on their outline, so things
    drawn inside them stay easy to select."""

    def _margin(self) -> float:
        return self.style.width / 2 + HIT_SLOP + 2  # bounds must contain the hit area

    def _outline(self) -> QPainterPath:
        raise NotImplementedError

    def shape(self) -> QPainterPath:
        if self.style.fill:
            return self._outline()
        return stroke_band(self._outline(), self.style.width + HIT_SLOP * 2)

    def paint(self, p: QPainter, _opt, _widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(style_pen(self.style))
        p.setBrush(fill_qcolor(self.kind, self.style) if self.style.fill else Qt.NoBrush)
        p.drawPath(self._outline())


def shape_path(shape: str, r: QRectF, corner: float = 3.0) -> QPainterPath:
    """The outline of a rectangle tool's shape in `r`: a box with `corner` rounding, a diamond, a
    triangle (point up) or a five-point star. Anything else is the rounded box."""
    path = QPainterPath()
    if shape == "diamond":
        c = r.center()
        path.addPolygon(QPolygonF([QPointF(c.x(), r.top()), QPointF(r.right(), c.y()),
                                   QPointF(c.x(), r.bottom()), QPointF(r.left(), c.y())]))
        path.closeSubpath()
    elif shape == "triangle":
        path.addPolygon(QPolygonF([QPointF(r.center().x(), r.top()), r.bottomRight(), r.bottomLeft()]))
        path.closeSubpath()
    elif shape == "star":
        c, outer = r.center(), (r.width() / 2, r.height() / 2)
        points = []
        for n in range(10):
            angle = -math.pi / 2 + n * math.pi / 5
            scale = 1.0 if n % 2 == 0 else 0.4
            points.append(QPointF(c.x() + outer[0] * scale * math.cos(angle),
                                  c.y() + outer[1] * scale * math.sin(angle)))
        path.addPolygon(QPolygonF(points))
        path.closeSubpath()
    else:
        radius = min(corner, r.width() / 2, r.height() / 2)
        path.addRoundedRect(r, radius, radius)
    return path


class RectItem(_OutlineShape):
    """The Rectangle tool: a box with rounded or square corners, or a diamond, triangle or star."""

    kind = "rect"

    def _corner(self) -> float:
        if self.style.corner != USUAL_CORNER:
            return float(self.style.corner)
        return 0.0 if self.style.shape == "rect" else 3.0  # as always: a small rounding

    def _outline(self) -> QPainterPath:
        return shape_path(self.style.shape, self._rect, self._corner())


class EllipseItem(_OutlineShape):
    kind = "ellipse"

    def _outline(self) -> QPainterPath:
        path = QPainterPath()
        path.addEllipse(self._rect)
        return path


class HidesContent:
    """For markup that hides part of the snip (blur, pixelate, smart erase). A magnifier draws it into
    its lens, and keeps a picture of itself while it has a shadow, so any change to this markup
    (moved, resized, restyled, added, removed, undone) redraws every lens."""

    hides_content = True

    def _follow_lenses(self) -> None:
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges)

    def _redraw_lenses(self, scene=None) -> None:
        scene = scene if scene is not None else self.scene()
        if scene is None:
            return
        for item in scene.items():
            if getattr(item, "kind", "") == "magnify":
                item.update()

    def _geometry_changed(self) -> None:
        super()._geometry_changed()
        self._redraw_lenses()

    def set_style(self, style: Style) -> None:
        super().set_style(style)
        self._redraw_lenses()

    def itemChange(self, change, value):  # noqa: N802 - Qt API
        if change == QGraphicsItem.ItemSceneChange and self.scene() is not None:
            self._redraw_lenses()  # leaving a scene: its lenses no longer show it
        elif change in (QGraphicsItem.ItemPositionHasChanged, QGraphicsItem.ItemSceneHasChanged,
                        QGraphicsItem.ItemVisibleHasChanged, QGraphicsItem.ItemZValueHasChanged):
            self._redraw_lenses()
        return super().itemChange(change, value)


class EffectItem(HidesContent, BoxItem):
    """Redaction region that pixelates or blurs the screenshot beneath it."""

    supports_shadow = False
    keep_square_with_shift = False

    def __init__(self, style: Style, base: QImage, mode: str) -> None:
        self._base = base
        self.mode = mode
        self._cache: tuple[tuple, QImage] | None = None
        super().__init__(style)
        self._follow_lenses()

    @property
    def kind(self) -> str:  # type: ignore[override]
        return self.mode

    def set_base(self, base: QImage) -> None:
        """The snip was turned or resized: blur or pixelate the new pixels."""
        self._base = base
        self._cache = None
        self.update()

    def _margin(self) -> float:
        return 1.0

    ROUNDING = 10.0  # the corners, with Rounded corners on

    def shape(self) -> QPainterPath:
        if not self.style.rounded:
            return super().shape()
        path = QPainterPath()
        path.addRoundedRect(self._rect, self.ROUNDING, self.ROUNDING)
        return path

    def paint(self, p: QPainter, _opt, _widget=None) -> None:
        area = self.scene_rect().toAlignedRect().intersected(self._base.rect())
        if area.isEmpty():
            return
        if self.style.rounded:
            p.setRenderHint(QPainter.Antialiasing)
            p.setClipPath(self.shape())
        erases = self._erases_under(area)
        key = (area.x(), area.y(), area.width(), area.height(), self.mode, self.style.width,
               tuple((e.area().getRect(), e.zValue()) for e in erases))
        if self._cache is None or self._cache[0] != key:
            self._cache = (key, self._render(area, erases))
        p.drawImage(self.mapRectFromScene(QRectF(area)), self._cache[1])

    def _erases_under(self, area) -> list:
        """Smart erases overlapping this redaction: it blurs the snip as erased, so what an erase
        removed never comes back through a blur on top of it."""
        scene = self.scene()
        if scene is None:
            return []
        return [i for i in scene.items() if getattr(i, "kind", "") == "erase" and i.isVisible()
                and i.sceneBoundingRect().intersects(QRectF(area))]

    def _render(self, area, erases=()) -> QImage:
        from .erase import bake  # here, not at the top: erase.py builds on this module
        sub = bake(self._base, erases).copy(area) if erases else self._base.copy(area)
        strength = self.style.width
        if self.mode == "pixelate":
            block = int(4 + strength * 2.5)
            small = sub.scaled(max(1, area.width() // block), max(1, area.height() // block),
                               Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            return small.scaled(area.size(), Qt.IgnoreAspectRatio, Qt.FastTransformation)
        factor = 3 + strength * 1.5
        small = sub.scaled(max(1, int(area.width() / factor)), max(1, int(area.height() / factor)),
                           Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        mid = small.scaled(max(1, area.width() // 2), max(1, area.height() // 2),
                           Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        return mid.scaled(area.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


class SpotlightItem(BoxItem):
    """Dims everything outside the rectangle."""

    kind = "spotlight"

    def selection_rects(self) -> list[QRectF]:
        """The overlay outlines the lit area, not the whole dimmed snip."""
        return [self.scene_rect()]
    supports_shadow = False
    keep_square_with_shift = False

    def __init__(self, style: Style, image_rect: QRectF) -> None:
        self._image_rect = QRectF(image_rect)
        super().__init__(style)
        self.setZValue(-500)  # under other annotations so they stay lit

    def set_image_rect(self, image_rect: QRectF) -> None:
        self.prepareGeometryChange()
        self._image_rect = QRectF(image_rect)
        self.update()

    def boundingRect(self) -> QRectF:
        return self.mapRectFromScene(self._image_rect).united(self._rect).adjusted(-2, -2, 2, 2)

    USUAL_ALPHA = 150

    def dim_alpha(self) -> int:
        """How dark the rest of the snip is, 0 to 255."""
        return self.USUAL_ALPHA if self.style.dim == USUAL_DIM else round(self.style.dim * 255 / 100)

    def lit_path(self) -> QPainterPath:
        """The area that stays lit, in item coordinates: a box (rounded a little, or a lot) or an ellipse."""
        hole = QPainterPath()
        if self.style.shape == "ellipse":
            hole.addEllipse(self._rect)
        else:
            radius = 14 if self.style.rounded else 4
            hole.addRoundedRect(self._rect, radius, radius)
        return hole

    def paint(self, p: QPainter, _opt, _widget=None) -> None:
        outer = QPainterPath()
        outer.addRect(self.mapRectFromScene(self._image_rect))
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, self.dim_alpha()))
        p.drawPath(outer.subtracted(self.lit_path()))


# ---------------------------------------------------------------------------
# Line-shaped items
# ---------------------------------------------------------------------------
class LineItem(Annotation, QGraphicsItem):
    """A straight line, or a curve (style.curve) with a bend you drag by its handle."""

    kind = "line"

    def __init__(self, style: Style, start: QPointF | None = None) -> None:
        QGraphicsItem.__init__(self)
        start = start or QPointF()
        self._p1 = QPointF(start)
        self._p2 = QPointF(start)
        self._control: QPointF | None = None  # the bend, once dragged; None: halfway, so it looks straight
        self.init_annotation(style)

    def set_points(self, p1: QPointF, p2: QPointF) -> None:
        self.prepareGeometryChange()
        self._p1, self._p2 = QPointF(p1), QPointF(p2)
        self.update()

    def set_end_scene(self, scene_pos: QPointF, shift: bool) -> None:
        end = self.mapFromScene(scene_pos)
        self.set_points(self._p1, constrain_45(self._p1, end) if shift else end)

    def length(self) -> float:
        return math.hypot(self._p2.x() - self._p1.x(), self._p2.y() - self._p1.y())

    def is_degenerate(self) -> bool:
        return self.length() < 4

    def set_style(self, style: Style) -> None:
        if not style.curve:
            self._control = None  # straight again: the bend is forgotten
        super().set_style(style)

    def control(self) -> QPointF:
        """The bend, in item coordinates (halfway along until it's dragged)."""
        return QPointF(self._control) if self._control is not None else (self._p1 + self._p2) / 2

    def _geometry(self) -> dict:
        geometry = {"p1": [self._p1.x(), self._p1.y()], "p2": [self._p2.x(), self._p2.y()]}
        if self.style.curve and self._control is not None:
            geometry["control"] = [self._control.x(), self._control.y()]
        return geometry

    def _set_geometry(self, state: dict) -> None:
        self._p1, self._p2 = QPointF(*state["p1"]), QPointF(*state["p2"])
        control = state.get("control")
        self._control = QPointF(*control) if self.style.curve and control is not None else None

    def _extra(self) -> float:
        return self.style.width / 2 + HIT_SLOP + 2  # bounds must contain the hit area

    def boundingRect(self) -> QRectF:
        m = self._extra()
        return self._path().boundingRect().adjusted(-m, -m, m, m)

    def _path(self) -> QPainterPath:
        """From p1 to p2: a line, or a curve through the bend."""
        path = QPainterPath(self._p1)
        if self.style.curve:
            path.quadTo(self.control(), self._p2)
        else:
            path.lineTo(self._p2)
        return path

    def shape(self) -> QPainterPath:
        return stroke_band(self._path(), self.style.width + HIT_SLOP * 2)

    def handle_points(self) -> dict[str, QPointF]:
        points = {"p1": self.mapToScene(self._p1), "p2": self.mapToScene(self._p2)}
        if self.style.curve:
            points["control"] = self.mapToScene(self.control())
        return points

    def drag_handle(self, name: str, scene_pos: QPointF, shift: bool) -> None:
        point = self.mapFromScene(scene_pos)
        if name == "control":
            self.prepareGeometryChange()
            self._control = point
            self.update()
        elif name == "p1":
            self.set_points(constrain_45(self._p2, point) if shift else point, self._p2)
        else:
            self.set_points(self._p1, constrain_45(self._p1, point) if shift else point)

    def paint(self, p: QPainter, _opt, _widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(style_pen(self.style))
        p.setBrush(Qt.NoBrush)
        p.drawPath(self._path())


class ArrowItem(LineItem):
    """A line with a head at its end, its start or both, filled or an open V; straight or curved."""

    kind = "arrow"

    def _head(self) -> float:
        return max(14.0, self.style.width * 4.2)

    def _extra(self) -> float:
        return self._head() + self.style.width + HIT_SLOP

    def _ends(self) -> list[tuple[QPointF, float]]:
        """Each head's tip and the angle it points in (along the path, outwards)."""
        control = self.control() if self.style.curve else None
        ends = []
        if self.style.heads in ("end", "both"):
            towards = control if control is not None else self._p1
            ends.append((self._p2, math.atan2(self._p2.y() - towards.y(), self._p2.x() - towards.x())))
        if self.style.heads in ("start", "both"):
            towards = control if control is not None else self._p2
            ends.append((self._p1, math.atan2(self._p1.y() - towards.y(), self._p1.x() - towards.x())))
        return ends

    def paint(self, p: QPainter, _opt, _widget=None) -> None:
        color = QColor(self.style.color)
        head = min(self._head(), self.length() * 0.6)
        wing = math.pi / 7
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(style_pen(self.style))
        p.setBrush(Qt.NoBrush)
        p.drawPath(self._path())
        for tip, angle in self._ends():
            left = QPointF(tip.x() - head * math.cos(angle - wing), tip.y() - head * math.sin(angle - wing))
            right = QPointF(tip.x() - head * math.cos(angle + wing), tip.y() - head * math.sin(angle + wing))
            if self.style.open_head:
                p.setPen(make_pen(color, self.style.width))
                p.drawLine(left, tip)
                p.drawLine(right, tip)
                continue
            neck = QPointF(tip.x() - head * 0.75 * math.cos(angle), tip.y() - head * 0.75 * math.sin(angle))
            p.setPen(make_pen(color, 1.5))
            p.setBrush(QBrush(color))
            p.drawPolygon(QPolygonF([tip, left, neck, right]))
            p.setPen(style_pen(self.style))
            p.setBrush(Qt.NoBrush)


# ---------------------------------------------------------------------------
# Freehand
# ---------------------------------------------------------------------------
class StrokeItem(Annotation, QGraphicsItem):
    """Freehand pen, or a highlighter that multiplies like a real marker."""

    def __init__(self, style: Style, highlighter: bool, start: QPointF | None = None) -> None:
        QGraphicsItem.__init__(self)
        self.highlighter = highlighter
        self._points: list[QPointF] = [QPointF(start)] if start is not None else []
        self.init_annotation(style)
        if highlighter:
            self.setZValue(-100)

    @property
    def kind(self) -> str:  # type: ignore[override]
        return "highlight" if self.highlighter else "pen"

    @property
    def supports_shadow(self) -> bool:  # type: ignore[override]
        return not self.highlighter

    def extend(self, point: QPointF) -> None:
        self.prepareGeometryChange()
        self._points.append(QPointF(point))
        self.update()

    def is_degenerate(self) -> bool:
        return len(self._points) < 2

    def _geometry(self) -> dict:
        return {"points": [[pt.x(), pt.y()] for pt in self._points]}

    def _set_geometry(self, state: dict) -> None:
        self._points = [QPointF(x, y) for x, y in state["points"]]

    def _pen_width(self) -> float:
        return self.style.width * 4 + 6 if self.highlighter else self.style.width

    def _path(self) -> QPainterPath:
        path = QPainterPath()
        if self._points:
            path.moveTo(self._points[0])
            for pt in self._points[1:]:
                path.lineTo(pt)
        return path

    def boundingRect(self) -> QRectF:
        m = self._pen_width() / 2 + HIT_SLOP / 2 + 2  # bounds must contain the hit area
        return self._path().boundingRect().adjusted(-m, -m, m, m)

    def shape(self) -> QPainterPath:
        return stroke_band(self._path(), self._pen_width() + HIT_SLOP)

    def paint(self, p: QPainter, _opt, _widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing)
        color = QColor(self.style.color)
        if self.highlighter:
            color.setAlpha(170)
            pen = QPen(color, self._pen_width(), Qt.SolidLine, Qt.FlatCap, Qt.RoundJoin)
            p.setCompositionMode(QPainter.CompositionMode_Multiply)
        else:
            pen = make_pen(color, self._pen_width())
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(self._path())


class HighlightBoxItem(BoxItem):
    """The highlighter's other form: a translucent box that multiplies like a marker over a whole area."""

    kind = "highlight_box"
    supports_shadow = False
    keep_square_with_shift = False

    def __init__(self, style: Style) -> None:
        super().__init__(style)
        self.setZValue(-100)  # under other markup, like highlighter strokes

    def _margin(self) -> float:
        return 2.0

    def paint(self, p: QPainter, _opt, _widget=None) -> None:
        color = QColor(self.style.color)
        color.setAlpha(170)
        p.setRenderHint(QPainter.Antialiasing)
        p.setCompositionMode(QPainter.CompositionMode_Multiply)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(self._rect, 2, 2)


# ---------------------------------------------------------------------------
# Step badge
# ---------------------------------------------------------------------------
def roman(n: int) -> str:
    """1 → I, 4 → IV, 14 → XIV; 0 and negatives stay digits."""
    if n <= 0:
        return str(n)
    out, rest = "", n
    for value, numeral in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"),
                           (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        count, rest = divmod(rest, value)
        out += numeral * count
    return out


def letters(n: int) -> str:
    """1 → A, 26 → Z, 27 → AA; 0 and negatives stay digits."""
    if n <= 0:
        return str(n)
    out, rest = "", n
    while rest:
        rest, r = divmod(rest - 1, 26)
        out = chr(ord("A") + r) + out
    return out



class StepItem(Annotation, QGraphicsItem):
    """A numbered badge: 1 2 3, A B C or I II III, in a circle, square or pill."""

    kind = "step"

    def __init__(self, style: Style, number: int = 1) -> None:
        QGraphicsItem.__init__(self)
        self.number = number
        self.init_annotation(style)

    def _d(self) -> float:
        return 18.0 + self.style.width * 4

    def label(self) -> str:
        if self.style.numbering == "letters":
            return letters(self.number)
        if self.style.numbering == "roman":
            return roman(self.number)
        return str(self.number)

    def _geometry(self) -> dict:
        return {"number": self.number}

    def _set_geometry(self, state: dict) -> None:
        self.number = int(state["number"])

    def _badge_rect(self) -> QRectF:
        """Wide enough for the label (a pill for XIV, say), never smaller than the circle."""
        d = self._d()
        text = QFontMetricsF(self._font()).horizontalAdvance(self.label())
        w = max(d, text + d * 0.55) if self.style.badge == "pill" or len(self.label()) > 2 else d
        return QRectF(-w / 2, -d / 2, w, d)

    def _font(self):
        font = ui_font(max(8, int(self._d() * 0.36)))
        font.setBold(True)
        return font

    def boundingRect(self) -> QRectF:
        return self._badge_rect().adjusted(-2, -2, 2, 2)

    def _badge_path(self) -> QPainterPath:
        r, path = self._badge_rect(), QPainterPath()
        if self.style.badge == "square":
            path.addRoundedRect(r, r.height() * 0.18, r.height() * 0.18)
        else:
            path.addRoundedRect(r, r.height() / 2, r.height() / 2)  # a circle, or a pill when wide
        return path

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self.boundingRect())
        return path

    def paint(self, p: QPainter, _opt, _widget=None) -> None:
        color = QColor(self.style.color)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.setBrush(color)
        p.drawPath(self._badge_path())
        p.setFont(self._font())
        p.setPen(readable_on(color))
        p.drawText(self._badge_rect(), Qt.AlignCenter, self.label())
