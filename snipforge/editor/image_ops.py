"""Rotate, flip and resize the snip, with its markup following along.

The editor never changes the captured pixels. It keeps the original and an ImageGeometry: how many
quarter-turns, whether it's mirrored, and the size. The image you mark up is drawn from the original
in one step, so rotating or resizing again and again never blurs it more than once, and the saved
document reopens exactly as you left it.

Markup follows each change. Shapes, lines and redactions turn and scale with the image; text,
callouts, stamps and step numbers move with it but stay upright, so they stay readable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from PySide6.QtCore import QPointF, QRectF, QSize, QSizeF, Qt
from PySide6.QtGui import QImage, QTransform

# The biggest image a rotate or resize makes: the same as a scrolling capture's limits (stitch.py). A
# capture that's bigger still can always be turned and flipped, and resized up to its own size.
MAX_SIDE = 30_000
MAX_PIXELS = 60_000_000
UPRIGHT_KINDS = {"text", "callout", "stamp", "step"}  # move with the image but never turn
STRENGTH = {"pixelate": (4.0, 2.5), "blur": (3.0, 1.5)}  # effect size = a + b * strength (items.EffectItem)
MIN_FONT = 6


@dataclass(frozen=True)
class ImageGeometry:
    """The original, mirrored left to right if `mirrored`, then turned `turns` quarter-turns
    clockwise, then scaled to `width` x `height`."""

    width: int
    height: int
    turns: int = 0
    mirrored: bool = False

    @staticmethod
    def of(image: QImage) -> ImageGeometry:
        return ImageGeometry(image.width(), image.height())

    def is_original(self, original: QImage) -> bool:
        return not self.turns and not self.mirrored and QSize(self.width, self.height) == original.size()

    def to_dict(self) -> dict:
        return {"turns": self.turns, "mirrored": self.mirrored, "size": [self.width, self.height]}

    @staticmethod
    def from_dict(data, original: QSize) -> ImageGeometry | None:
        """None for anything that isn't a geometry this version can draw from `original`."""
        if not isinstance(data, dict):
            return None
        turns, mirrored, size = data.get("turns", 0), data.get("mirrored", False), data.get("size")
        if type(turns) is not int or not 0 <= turns <= 3 or not isinstance(mirrored, bool):
            return None
        if not isinstance(size, list) or len(size) != 2 or not all(type(v) is int for v in size):
            return None
        return ImageGeometry(size[0], size[1], turns, mirrored) if fits(*size, original) else None


def fits(width: int, height: int, original: QSize | None = None) -> bool:
    """Whether SnipForge makes an image this size (from `original`, which may itself be bigger)."""
    side, pixels = MAX_SIDE, MAX_PIXELS
    if original is not None:
        side = max(side, original.width(), original.height())
        pixels = max(pixels, original.width() * original.height())
    return 1 <= width <= side and 1 <= height <= side and width * height <= pixels


def render(original: QImage, geometry: ImageGeometry) -> QImage:
    """The image to mark up: the original drawn with `geometry`, resampled at most once."""
    image = original
    if geometry.mirrored:
        image = image.flipped(Qt.Horizontal)
    if geometry.turns:
        image = image.transformed(QTransform().rotate(90 * geometry.turns))
    if image.size() != QSize(geometry.width, geometry.height):
        image = image.scaled(geometry.width, geometry.height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return image


# ---------------------------------------------------------------------------
# One change at a time: the new geometry and where every point goes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Remap:
    """Where a point on the old image lands on the new one (`transform`), and how much a length
    along x and y grows (`sx`, `sy`)."""

    transform: QTransform
    sx: float = 1.0
    sy: float = 1.0


def rotate(geometry: ImageGeometry, clockwise: bool) -> tuple[ImageGeometry, Remap]:
    w, h = geometry.width, geometry.height
    turns = (geometry.turns + (1 if clockwise else -1)) % 4
    # Clockwise in screen coordinates (y down): (x, y) -> (h - y, x). Anticlockwise: (y, w - x).
    t = QTransform(0, 1, -1, 0, h, 0) if clockwise else QTransform(0, -1, 1, 0, 0, w)
    return replace(geometry, width=h, height=w, turns=turns), Remap(t)


def flip(geometry: ImageGeometry, horizontal: bool) -> tuple[ImageGeometry, Remap]:
    """Left to right if `horizontal`, else top to bottom. A mirror after a turn is the mirror
    first and the opposite turn after it, so the geometry stays mirror-then-turn."""
    w, h = geometry.width, geometry.height
    turns = (-geometry.turns) % 4 if horizontal else (2 - geometry.turns) % 4
    t = QTransform(-1, 0, 0, 1, w, 0) if horizontal else QTransform(1, 0, 0, -1, 0, h)
    return replace(geometry, turns=turns, mirrored=not geometry.mirrored), Remap(t)


def resize(geometry: ImageGeometry, width: int, height: int) -> tuple[ImageGeometry, Remap]:
    sx, sy = width / geometry.width, height / geometry.height
    return replace(geometry, width=width, height=height), Remap(QTransform.fromScale(sx, sy), sx, sy)


def snap(rect: QRectF, bounds: QRectF) -> QRectF:
    """Whole pixels, inside the image."""
    left, top = round(rect.left()), round(rect.top())
    snapped = QRectF(left, top, round(rect.right()) - left, round(rect.bottom()) - top)
    return snapped.intersected(bounds)


# ---------------------------------------------------------------------------
# Markup
# ---------------------------------------------------------------------------
def scaled_style(kind: str, style: dict, sx: float, sy: float | None = None) -> dict:
    """Line widths and font sizes grow and shrink with the image, so it looks the same. A blur or
    pixelation keeps covering at least as much of what's under it along both axes, so nothing it
    hid ever becomes readable."""
    sy = sx if sy is None else sy
    if sx == sy == 1.0:
        return style
    out = dict(style)
    width = float(style.get("width", 4.0))
    if kind in STRENGTH:
        a, b = STRENGTH[kind]
        block = math.ceil((a + b * width) * max(sx, sy) - 1e-9)  # EffectItem rounds its block down
        out["width"] = max(1.0, (block - a) / b + 1e-6)
    else:
        out["width"] = max(1.0, width * math.sqrt(sx * sy))
    if "font_size" in style:
        out["font_size"] = max(MIN_FONT, round(int(style["font_size"]) * math.sqrt(sx * sy)))
    return out


def mapped_state(item, remap: Remap) -> dict:
    """The item's state once the image has changed by `remap`."""
    state = item.get_state()
    kind, t = state["kind"], remap.transform
    pos = QPointF(*state["pos"])
    old_style = state["style"]
    state["style"] = scaled_style(kind, old_style, remap.sx, remap.sy)
    if kind in UPRIGHT_KINDS:
        _map_upright(item, state, pos, remap, old_style)
    elif "size" in state:
        rect = t.mapRect(QRectF(pos, QSizeF(*state["size"])))
        state["pos"], state["size"] = [rect.x(), rect.y()], [rect.width(), rect.height()]
    elif "p1" in state:
        new_pos = t.map(pos)
        p1, p2 = (t.map(pos + QPointF(*state[key])) - new_pos for key in ("p1", "p2"))
        p2 = _long_enough(p1, p2)
        state["p1"], state["p2"] = [p1.x(), p1.y()], [p2.x(), p2.y()]
        state["pos"] = [new_pos.x(), new_pos.y()]
    elif "points" in state:
        new_pos = t.map(pos)
        moved = (t.map(pos + QPointF(x, y)) - new_pos for x, y in state["points"])
        state["points"] = [[p.x(), p.y()] for p in moved]
        state["pos"] = [new_pos.x(), new_pos.y()]
    else:
        new_pos = t.map(pos)
        state["pos"] = [new_pos.x(), new_pos.y()]
    if "size" in state:
        _big_enough(item, state)
    if "source" in state:  # a magnifier's source area, in scene coordinates
        source = t.mapRect(QRectF(*state["source"]))
        grow_w, grow_h = max(0.0, MIN_BOX - source.width()), max(0.0, MIN_BOX - source.height())
        source.adjust(-grow_w / 2, -grow_h / 2, grow_w / 2, grow_h / 2)
        state["source"] = [source.x(), source.y(), source.width(), source.height()]
    return state


MIN_BOX = 6.0  # the smallest a shrunk box (or a magnifier's source) is kept


MIN_LINE = 6.0  # a shrunk line is kept at least this long (LineItem drops lines under 4 px)


def _long_enough(p1: QPointF, p2: QPointF) -> QPointF:
    """A line shrunk to almost nothing would be dropped when saved: keep it MIN_LINE long."""
    dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
    length = math.hypot(dx, dy)
    if length >= MIN_LINE:
        return p2
    if length < 1e-6:
        return QPointF(p1.x() + MIN_LINE, p1.y())
    return QPointF(p1.x() + dx * MIN_LINE / length, p1.y() + dy * MIN_LINE / length)


def _big_enough(item, state: dict) -> None:
    """A box shrunk below its smallest size would be dropped when saved (and a redaction with it):
    grow it back to that size around its centre."""
    min_w, min_h = getattr(item, "min_size", (6.0, 6.0))
    x, y = state["pos"]
    w, h = state["size"]
    new_w, new_h = max(w, min_w), max(h, min_h)
    if getattr(item, "always_square", False):
        new_w = new_h = max(new_w, new_h)
    if (new_w, new_h) == (w, h):
        return
    state["pos"] = [x - (new_w - w) / 2, y - (new_h - h) / 2]
    state["size"] = [new_w, new_h]
    if "tail" in state:  # the callout's pointer stays where it pointed
        state["tail"] = [state["tail"][0] + (new_w - w) / 2, state["tail"][1] + (new_h - h) / 2]


def _map_upright(item, state: dict, pos: QPointF, remap: Remap, old_style: dict) -> None:
    """Keep the item's centre over the same spot, scaled but not turned."""
    t = remap.transform
    if "size" in state:  # callout, stamp: a box
        w, h = state["size"]
        w, h = w * remap.sx, h * remap.sy
        if state["kind"] == "stamp":
            w = h = min(w, h)
        center = t.map(pos + QPointF(state["size"][0] / 2, state["size"][1] / 2))
        top_left = center - QPointF(w / 2, h / 2)
        if "tail" in state:  # the callout's pointer keeps pointing at the same thing
            tip = t.map(pos + QPointF(*state["tail"])) - top_left
            state["tail"] = [tip.x(), tip.y()]
        state["pos"], state["size"] = [top_left.x(), top_left.y()], [w, h]
    elif state["kind"] == "text":  # positioned by its top-left corner; its size comes from the font
        box = item.boundingRect()
        center = t.map(pos + box.center())
        margin = 2 * item.document().documentMargin()  # doesn't scale with the font
        k = state["style"]["font_size"] / max(1, int(old_style["font_size"]))
        w, h = (box.width() - margin) * k + margin, (box.height() - margin) * k + margin
        top_left = center - QPointF(w / 2, h / 2)
        state["pos"] = [top_left.x(), top_left.y()]
    else:  # step: positioned by its centre
        center = t.map(pos)
        state["pos"] = [center.x(), center.y()]
