"""Immutable annotation style and per-tool defaults."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace

PALETTE = ("#ff3b30", "#ff9500", "#ffd60a", "#34c759", "#0a84ff", "#bf5af2", "#ffffff", "#1c1c1e")
CALLOUT_SHAPES = ("rounded", "rect", "ellipse", "pill")


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
        known = {f.name for f in fields(Style)}
        return Style(**{k: v for k, v in data.items() if k in known})

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
}

STRENGTH_KINDS = {"pixelate", "blur"}  # 'width' means redaction strength for these
