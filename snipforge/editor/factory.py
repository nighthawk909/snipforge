"""Build any annotation from its saved state."""
from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage

from .erase import EraseItem
from .items import (
    Annotation,
    ArrowItem,
    EffectItem,
    EllipseItem,
    LineItem,
    RectItem,
    SpotlightItem,
    StepItem,
    StrokeItem,
)
from .magnify import MagnifyItem
from .stamps import StampItem
from .style import Style
from .text_items import CalloutItem, TextItem


def create_item(state: dict, base_image: QImage, image_rect: QRectF) -> Annotation:
    style = Style.from_dict(state["style"])
    kind = state["kind"]
    builders = {
        "rect": lambda: RectItem(style),
        "ellipse": lambda: EllipseItem(style),
        "pixelate": lambda: EffectItem(style, base_image, "pixelate"),
        "blur": lambda: EffectItem(style, base_image, "blur"),
        "spotlight": lambda: SpotlightItem(style, image_rect),
        "line": lambda: LineItem(style),
        "arrow": lambda: ArrowItem(style),
        "pen": lambda: StrokeItem(style, highlighter=False),
        "highlight": lambda: StrokeItem(style, highlighter=True),
        "step": lambda: StepItem(style),
        "text": lambda: TextItem(style),
        "callout": lambda: CalloutItem(style),
        "stamp": lambda: StampItem(style),
        "magnify": lambda: MagnifyItem(style, base_image),
        "erase": lambda: EraseItem(style, base_image),
    }
    if kind not in builders:
        raise ValueError(f"Unknown annotation kind: {kind!r}")
    item = builders[kind]()
    item.set_state(state)
    return item
