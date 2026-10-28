"""Immutable annotation style and per-tool defaults."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields, replace

from PySide6.QtGui import QColor

PALETTE = ("#ff3b30", "#ff9500", "#ffd60a", "#34c759", "#0a84ff", "#bf5af2", "#ffffff", "#1c1c1e")
COLOR_NAMES = {"#ff3b30": "Red", "#ff9500": "Orange", "#ffd60a": "Yellow", "#34c759": "Green",
               "#0a84ff": "Blue", "#bf5af2": "Purple", "#ffffff": "White", "#1c1c1e": "Black"}
CALLOUT_SHAPES = ("rounded", "rect", "ellipse", "pill")
RECT_SHAPES = ("rounded", "rect", "diamond", "triangle", "star")  # the Rectangle tool's shapes
SPOTLIGHT_SHAPES = ("rect", "ellipse")
SHAPES = tuple(dict.fromkeys((*CALLOUT_SHAPES, *RECT_SHAPES, *SPOTLIGHT_SHAPES)))  # every value `shape` can take
ARROW_HEADS = ("end", "start", "both")  # where an arrow's heads are
NUMBERINGS = ("digits", "letters", "roman")  # step numbers: 1 2 3, A B C, I II III
BADGES = ("circle", "square", "pill")  # a step number's badge
ALIGNMENTS = ("", "left", "center", "right")  # "": the usual one (text left, callouts centered)
USUAL_CORNER = -1  # corner: the tool's usual rounding (a rectangle's small one)
USUAL_DIM = -1  # dim: the spotlight's usual darkness
CORNER_RANGE = (0, 60)
DIM_RANGE = (0, 100)
WIDTH_RANGE = (0.5, 200.0)  # wider than the panel's slider: a resized snip scales widths
FONT_RANGE = (4, 400)
USUAL_OPACITY = -1  # fill_opacity: the tool's usual fill (a light tint for shapes, solid otherwise)
SHAPE_TINT_ALPHA = 70  # the usual fill of rectangles and ellipses: a light tint of their color
TINTED_KINDS = {"rect", "ellipse"}


def _number(value):
    """`value` if it's a real number (not a bool, NaN or infinity), else None. Huge integers pass:
    callers clamp before converting, so damaged data can't overflow."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _clean(name: str, value):
    """`value` if it's a usable value for the field `name`, else None."""
    if name == "color":
        return value if isinstance(value, str) and QColor(value).isValid() else None
    if name in ("width", "font_size", "fill_opacity", "corner", "dim"):
        number = _number(value)
        if number is None:
            return None
        if name == "width":
            return float(min(WIDTH_RANGE[1], max(WIDTH_RANGE[0], number)))
        if name == "font_size":
            return int(min(FONT_RANGE[1], max(FONT_RANGE[0], number)))
        if name == "corner":
            return USUAL_CORNER if number < 0 else int(round(min(CORNER_RANGE[1], number)))
        if name == "dim":
            return USUAL_DIM if number < 0 else int(round(min(DIM_RANGE[1], number)))
        return USUAL_OPACITY if number < 0 else int(round(min(100, number)))
    if name == "fill_color":  # "" means the outline's color
        return value if isinstance(value, str) and (value == "" or QColor(value).isValid()) else None
    if name == "shape":
        return value if value in SHAPES else None
    if name in ("stamp", "font_family"):
        return value if isinstance(value, str) else None  # an unknown stamp draws the check; an unknown font, the usual
    words = {"heads": ARROW_HEADS, "numbering": NUMBERINGS, "badge": BADGES, "align": ALIGNMENTS}
    if name in words:
        return value if value in words[name] else None
    return value if isinstance(value, bool) else None  # shadow, fill, dash, dotted, tail, and the switches below


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
    bold: bool = True        # text boxes (Edit text writes regular weight, to match the snip)
    # Tool variants (document version 3 when any is used; see needs_version_3)
    heads: str = "end"       # arrows: where the heads are
    open_head: bool = False  # arrows: an open V instead of a filled head
    curve: bool = False      # arrows and lines: a bend you drag
    corner: int = USUAL_CORNER  # rectangles: corner rounding in px
    numbering: str = "digits"  # step numbers
    badge: str = "circle"    # step numbers' badge
    box: bool = False        # highlighter: a translucent box instead of a stroke
    font_family: str = ""    # text and callouts: "" is the usual font
    align: str = ""          # text and callouts: "" is the usual (usual_align)
    italic: bool = False
    underline: bool = False
    outline: bool = False    # text: a thin dark or light edge so it reads on anything
    rounded: bool = False    # blur, pixelate and spotlight: rounded corners
    dim: int = USUAL_DIM     # spotlight: how dark the rest is, in percent

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
TEXT_FORMAT = ("font_family", "align", "italic", "underline")
STYLE_FIELDS: dict[str, tuple[str, ...]] = {
    "arrow": ("heads", "open_head", "curve", "color", "width", "dash", "dotted", "shadow"),
    "line": ("curve", "color", "width", "dash", "dotted", "shadow"),
    "rect": ("shape", "corner", "color", "width", "dash", "dotted", "fill", "fill_color", "fill_opacity", "shadow"),
    "ellipse": ("color", "width", "dash", "dotted", "fill", "fill_color", "fill_opacity", "shadow"),
    "pen": ("color", "width", "shadow"),
    "highlight": ("box", "color", "width"),
    "highlight_box": ("color",),
    "text": ("color", "font_size", *TEXT_FORMAT, "bold", "outline", "fill", "fill_color", "fill_opacity", "shadow"),
    "callout": ("shape", "tail", "color", "width", "font_size", *TEXT_FORMAT, "fill", "fill_color", "fill_opacity",
                "shadow"),
    "stamp": ("stamp", "shadow"),
    "step": ("numbering", "badge", "color", "width", "shadow"),
    "pixelate": ("width", "rounded"),
    "blur": ("width", "rounded"),
    "spotlight": ("shape", "rounded", "dim"),
    "magnify": ("shape", "color", "width", "dash", "dotted", "shadow"),
    "erase": (),
}
VERSION_3_KINDS = {"highlight_box"}  # kinds older SnipForges can't draw
VERSION_3_DEFAULTS = {"heads": "end", "open_head": False, "curve": False, "corner": USUAL_CORNER, "numbering": "digits",
                      "badge": "circle", "box": False, "font_family": "", "align": "", "italic": False,
                      "underline": False, "outline": False, "rounded": False, "dim": USUAL_DIM}


def needs_version_3(style: dict, kind: str | None = None) -> bool:
    """Whether a saved style uses a tool variant an older SnipForge would draw wrong (or not at all)."""
    if not isinstance(style, dict):
        return False
    if any(style.get(k, v) != v for k, v in VERSION_3_DEFAULTS.items()):
        return True
    stamp = style.get("stamp")
    if isinstance(stamp, str) and stamp.startswith("custom:"):  # your own stamp: version 1 draws the check
        return True
    shape = style.get("shape")
    if kind in ("rect", "spotlight"):  # version 1 draws both as a rounded box whatever their shape
        return shape not in (None, "rounded")
    return shape in RECT_SHAPES[2:]  # diamond, triangle, star


def usual_align(kind: str) -> str:
    return "center" if kind == "callout" else "left"


def text_align(kind: str, style: Style) -> str:
    """How the text's lines line up: its own choice, else the tool's usual."""
    return style.align or usual_align(kind)

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
