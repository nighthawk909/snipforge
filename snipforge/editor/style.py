"""Immutable annotation style and per-tool defaults."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields, replace

from PySide6.QtGui import QColor

PALETTE = ("#ff3b30", "#ff9500", "#ffd60a", "#34c759", "#0a84ff", "#bf5af2", "#ffffff", "#1c1c1e")
COLOR_NAMES = {"#ff3b30": "Red", "#ff9500": "Orange", "#ffd60a": "Yellow", "#34c759": "Green",
               "#0a84ff": "Blue", "#bf5af2": "Purple", "#ffffff": "White", "#1c1c1e": "Black"}
CALLOUT_SHAPES = ("rounded", "rect", "ellipse", "pill")
WIDTH_RANGE = (0.5, 200.0)  # wider than the panel's slider: a resized snip scales widths
FONT_RANGE = (4, 400)
USUAL_OPACITY = -1  # fill_opacity: the tool's usual fill (a light tint for shapes, solid otherwise)
SHAPE_TINT_ALPHA = 70  # the usual fill of rectangles and ellipses: a light tint of their color
TINTED_KINDS = {"rect", "ellipse"}


def _clean(name: str, value):
    """`value` if it's a usable value for the field `name`, else None."""
    if name == "color":
        return value if isinstance(value, str) and QColor(value).isValid() else None
    if name == "width":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        return min(WIDTH_RANGE[1], max(WIDTH_RANGE[0], float(value)))
    if name == "font_size":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        return min(FONT_RANGE[1], max(FONT_RANGE[0], int(value)))
    if name == "fill_color":  # "" means the outline's color
        return value if isinstance(value, str) and (value == "" or QColor(value).isValid()) else None
    if name == "fill_opacity":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        return USUAL_OPACITY if value < 0 else min(100, int(round(value)))
    if name == "shape":
        return value if value in CALLOUT_SHAPES else None
    if name == "stamp":
        return value if isinstance(value, str) else None  # unknown names draw the check stamp
    return value if isinstance(value, bool) else None  # shadow, fill, dash, dotted, tail


@dataclass(frozen=True)
class Style:
    color: str = "#ff3b30"
    width: float = 4.0
    shadow: bool = True
    fill: bool = False
    font_size: int = 18
    dash: bool = False
    dotted: bool = False     # with dash: dots (older versions, which don't know it, draw dashes)
    fill_color: str = ""     # "" = the outline's color
    fill_opacity: int = USUAL_OPACITY  # percent, or USUAL_OPACITY
    shape: str = "rounded"   # callout body
    tail: bool = True        # callout pointer
    stamp: str = "check"     # stamp artwork

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> Style:
        """Unknown keys, and values of the wrong type or out of range, are left at their defaults:
        styles come from settings and saved documents, which may be damaged."""
        known = {f.name for f in fields(Style)}
        cleaned = {k: _clean(k, v) for k, v in data.items() if k in known} if isinstance(data, dict) else {}
        return Style(**{k: v for k, v in cleaned.items() if v is not None})

    def with_changes(self, **changes) -> Style:
        known = {f.name for f in fields(Style)}
        return replace(self, **{k: v for k, v in changes.items() if k in known})


DEFAULT_TOOL_STYLES: dict[str, Style] = {
    "arrow": Style(),
    "line": Style(),
    "rect": Style(),
    "ellipse": Style(),
    "pen": Style(width=3, shadow=False),
    "highlight": Style(color="#ffd60a", width=5, shadow=False),
    "text": Style(fill=True, font_size=18),
    "callout": Style(color="#0a84ff", width=2, fill=True, font_size=16),
    "stamp": Style(),
    "step": Style(width=4),
    "pixelate": Style(width=4, shadow=False),
    "blur": Style(width=4, shadow=False),
    "spotlight": Style(shadow=False),
    "magnify": Style(color="#1c1c1e", width=3, shape="ellipse"),
    "erase": Style(shadow=False),
}

# Which style controls the property bar shows for each annotation kind.
STYLE_FIELDS: dict[str, tuple[str, ...]] = {
    "arrow": ("color", "width", "dash", "dotted", "shadow"),
    "line": ("color", "width", "dash", "dotted", "shadow"),
    "rect": ("color", "width", "dash", "dotted", "fill", "fill_color", "fill_opacity", "shadow"),
    "ellipse": ("color", "width", "dash", "dotted", "fill", "fill_color", "fill_opacity", "shadow"),
    "pen": ("color", "width", "shadow"),
    "highlight": ("color", "width"),
    "text": ("color", "font_size", "fill", "fill_color", "fill_opacity", "shadow"),
    "callout": ("shape", "tail", "color", "width", "font_size", "fill", "fill_color", "fill_opacity", "shadow"),
    "stamp": ("stamp", "shadow"),
    "step": ("color", "width", "shadow"),
    "pixelate": ("width",),
    "blur": ("width",),
    "spotlight": (),
    "magnify": ("shape", "color", "width", "dash", "dotted", "shadow"),
    "erase": (),
}

STRENGTH_KINDS = {"pixelate", "blur"}  # 'width' means redaction strength for these


def line_style(style: Style) -> str:
    """"solid", "dashed" or "dotted"."""
    return ("dotted" if style.dotted else "dashed") if style.dash else "solid"


def usual_fill_opacity(kind: str) -> int:
    return round(SHAPE_TINT_ALPHA * 100 / 255) if kind in TINTED_KINDS else 100


def fill_opacity(kind: str, style: Style) -> int:
    """The fill's opacity in percent, as the panel shows it."""
    return usual_fill_opacity(kind) if style.fill_opacity == USUAL_OPACITY else style.fill_opacity


def fill_qcolor(kind: str, style: Style) -> QColor:
    """The fill as it's painted: its own color, or the outline's, at its opacity (or the tool's usual one)."""
    color = QColor(style.fill_color or style.color)
    if style.fill_opacity != USUAL_OPACITY:
        color.setAlpha(round(style.fill_opacity * 255 / 100))
    elif kind in TINTED_KINDS:
        color.setAlpha(SHAPE_TINT_ALPHA)
    return color
