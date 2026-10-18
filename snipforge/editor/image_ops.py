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

MAX_SIDE = 16384  # pixels on either side
MAX_PIXELS = 64_000_000  # 256 MB as 32-bit pixels: more than an 8K screenshot, less than runs out of memory
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
    def from_dict(data) -> ImageGeometry | None:
        """None for anything that isn't a geometry this version can draw."""
        if not isinstance(data, dict):
            return None
        turns, mirrored, size = data.get("turns", 0), data.get("mirrored", False), data.get("size")
        if type(turns) is not int or not 0 <= turns <= 3 or not isinstance(mirrored, bool):
            return None
        if not isinstance(size, list) or len(size) != 2 or not all(type(v) is int for v in size):
            return None
        return ImageGeometry(size[0], size[1], turns, mirrored) if fits(*size) else None


def fits(width: int, height: int) -> bool:
    """Whether an image this size is one SnipForge will make."""
    return 1 <= width <= MAX_SIDE and 1 <= height <= MAX_SIDE and width * height <= MAX_PIXELS


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

    @property
    def scale(self) -> float:
        """One factor for things that have a single size: line widths, font sizes."""
        return math.sqrt(self.sx * self.sy)


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
def scaled_style(kind: str, style: dict, k: float) -> dict:
    """Line widths and font sizes grow and shrink with the image, so it looks the same. A blur or
    pixelation keeps covering the same share of what's under it (never finer than before when
    the image grows, so nothing it hid becomes readable)."""
    if k == 1.0:
        return style
    out = dict(style)
    width = float(style.get("width", 4.0))
    if kind in STRENGTH:
        a, b = STRENGTH[kind]
        out["width"] = max(1.0, ((a + b * width) * k - a) / b)
    else:
        out["width"] = max(1.0, width * k)
    if "font_size" in style:
        out["font_size"] = max(MIN_FONT, round(int(style["font_size"]) * k))
    return out


def mapped_state(item, remap: Remap) -> dict:
    """The item's state once the image has changed by `remap`."""
    state = item.get_state()
    kind, t = state["kind"], remap.transform
    pos = QPointF(*state["pos"])
    state["style"] = scaled_style(kind, state["style"], remap.scale)
    if kind in UPRIGHT_KINDS:
        _map_upright(item, state, pos, remap)
    elif "size" in state:
        rect = t.mapRect(QRectF(pos, QSizeF(*state["size"])))
        state["pos"], state["size"] = [rect.x(), rect.y()], [rect.width(), rect.height()]
    elif "p1" in state:
        new_pos = t.map(pos)
        for key in ("p1", "p2"):
            point = t.map(pos + QPointF(*state[key])) - new_pos
            state[key] = [point.x(), point.y()]
        state["pos"] = [new_pos.x(), new_pos.y()]
    elif "points" in state:
        new_pos = t.map(pos)
        moved = (t.map(pos + QPointF(x, y)) - new_pos for x, y in state["points"])
        state["points"] = [[p.x(), p.y()] for p in moved]
        state["pos"] = [new_pos.x(), new_pos.y()]
    else:
        new_pos = t.map(pos)
        state["pos"] = [new_pos.x(), new_pos.y()]
    return state


def _map_upright(item, state: dict, pos: QPointF, remap: Remap) -> None:
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
        top_left = center - QPointF(box.width() * remap.scale / 2, box.height() * remap.scale / 2)
        state["pos"] = [top_left.x(), top_left.y()]
    else:  # step: positioned by its centre
        center = t.map(pos)
        state["pos"] = [center.x(), center.y()]
