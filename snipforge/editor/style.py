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
    if name == "shape":
        return value if value in CALLOUT_SHAPES else None
    if name == "stamp":
        return value if isinstance(value, str) else None  # unknown names draw the check stamp
    return value if isinstance(value, bool) else None  # shadow, fill, dash, tail


@dataclass(frozen=True)
class Style:
    color: str = "#ff3b30"
    width: float = 4.0
    shadow: bool = True
    fill: bool = False
    font_size: int = 18
    dash: bool = False
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
    "arrow": ("color", "width", "dash", "shadow"),
    "line": ("color", "width", "dash", "shadow"),
    "rect": ("color", "width", "dash", "fill", "shadow"),
    "ellipse": ("color", "width", "dash", "fill", "shadow"),
    "pen": ("color", "width", "shadow"),
    "highlight": ("color", "width"),
    "text": ("color", "font_size", "fill", "shadow"),
    "callout": ("shape", "tail", "color", "width", "font_size", "fill", "shadow"),
    "stamp": ("stamp", "shadow"),
    "step": ("color", "width", "shadow"),
    "pixelate": ("width",),
    "blur": ("width",),
    "spotlight": (),
    "magnify": ("shape", "color", "width", "dash", "shadow"),
    "erase": (),
}

STRENGTH_KINDS = {"pixelate", "blur"}  # 'width' means redaction strength for these
