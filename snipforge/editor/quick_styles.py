"""One-click annotation looks (SnagIt calls them quick styles)."""
from __future__ import annotations

from dataclasses import dataclass

from .style import STYLE_FIELDS, USUAL_OPACITY


@dataclass(frozen=True)
class QuickStyle:
    label: str
    color: str
    fill_on: bool  # filled shapes and callouts; outline otherwise

    def changes(self) -> dict:
        """Its exact look: a fill color or opacity you'd set goes back to the usual."""
        return {"color": self.color, "fill": self.fill_on, "fill_color": "", "fill_opacity": USUAL_OPACITY}


QUICK_STYLES: tuple[QuickStyle, ...] = (
    QuickStyle("Red outline", "#ff3b30", False),
    QuickStyle("Red", "#ff3b30", True),
    QuickStyle("Blue", "#0a84ff", True),
    QuickStyle("Yellow note", "#ffd60a", True),
    QuickStyle("Green", "#34c759", True),
    QuickStyle("Purple outline", "#bf5af2", False),
    QuickStyle("Black", "#1c1c1e", True),
    QuickStyle("Orange outline", "#ff9500", False),
)


def changes_for(kind: str, quick: QuickStyle) -> dict:
    """The quick style's changes, limited to what this kind of annotation can show."""
    fields = STYLE_FIELDS.get(kind, ())
    return {k: v for k, v in quick.changes().items() if k in fields}
