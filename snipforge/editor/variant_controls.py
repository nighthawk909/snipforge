"""The panel's controls for tool variants (style.py): an arrow's heads and curve, a rectangle's shape
and corners, a step number's numbering and badge, text formatting, and a spotlight's dimming.

Each control emits `changed` with the style fields to set, loads a style without echoing it back,
and rethemes its icons. The panel shows each one only for tools that have its fields.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFontComboBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..icons import icon
from ..widgets import Segmented, screen_reader_name
from .style import (
    ARROW_HEADS,
    BADGES,
    CALLOUT_SHAPES,
    CORNER_RANGE,
    DIM_RANGE,
    NUMBERINGS,
    RECT_SHAPES,
    SPOTLIGHT_SHAPES,
    USUAL_CORNER,
    USUAL_DIM,
    Style,
    text_align,
)

SHAPE_LABELS = {"rounded": "Rounded box", "rect": "Box", "ellipse": "Ellipse", "pill": "Pill", "diamond": "Diamond",
                "triangle": "Triangle", "star": "Star"}
HEAD_LABELS = {"end": "Head at the end", "start": "Head at the start", "both": "Heads at both ends"}
NUMBERING_LABELS = {"digits": "1 2 3", "letters": "A B C", "roman": "I II III"}
BADGE_LABELS = {"circle": "Circle", "square": "Square", "pill": "Pill"}
ALIGN_LABELS = {"left": "Align left", "center": "Center", "right": "Align right"}
SHAPES_FOR = {"rect": RECT_SHAPES, "callout": CALLOUT_SHAPES, "spotlight": SPOTLIGHT_SHAPES}
ICON_SEGMENT_MIN_WIDTH = 34  # a segment's least width, whatever its icon's or text's padding
FONT_BOX_MIN_CHARACTERS = 10  # the font list's least width: about "Segoe UI S"
USUAL_RECT_CORNER = 3  # what the slider shows for a rectangle's usual rounding


class FocusedSpinBox(QSpinBox):
    """Changed by the wheel only once you've clicked or tabbed into it: scrolling the panel over it
    mustn't change the markup."""

    def __init__(self) -> None:
        super().__init__()
        self.setFocusPolicy(Qt.StrongFocus)  # the wheel doesn't focus it either

    def wheelEvent(self, e) -> None:  # noqa: N802 - Qt API
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()  # the panel scrolls instead


class FocusedFontBox(QFontComboBox):
    """The font list, changed by the wheel only once it's focused (see FocusedSpinBox)."""

    def __init__(self) -> None:
        super().__init__()
        self.setFocusPolicy(Qt.StrongFocus)
        # Sized by a few letters, not the longest installed font's name, so it never widens the panel;
        # it still fills the row, and the open list shows names in full.
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(FONT_BOX_MIN_CHARACTERS)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def wheelEvent(self, e) -> None:  # noqa: N802 - Qt API
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()


def icon_segmented(options: tuple[str, ...], labels: dict[str, str], icon_prefix: str) -> Segmented:
    """A Segmented of icons, named in tooltips and for screen readers."""
    seg = Segmented([(key, labels[key]) for key in options], value=options[0])
    for key in options:
        b = seg.button(key)
        b.setToolTip(labels[key])
        b.setAccessibleName(labels[key])
        b.setText("")
        b.setToolButtonStyle(Qt.ToolButtonIconOnly)
        b.setIconSize(QSize(20, 20))
        b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        b.setMinimumWidth(ICON_SEGMENT_MIN_WIDTH)  # the text padding would make five icons wider than the panel
    seg.setProperty("iconPrefix", icon_prefix)
    return seg


def retheme_segmented(seg: Segmented, options: tuple[str, ...]) -> None:
    prefix = seg.property("iconPrefix")
    for key in options:
        seg.button(key).setIcon(icon(f"{prefix}_{key}"))


class ShapeControls(QWidget):
    """The shape for the tool at hand (a callout's, a rectangle's or a spotlight's), and a rectangle's
    corner rounding."""

    changed = Signal(dict)

    def __init__(self, slider_factory, parent=None) -> None:
        super().__init__(parent)
        self._loading = False
        self._kind = "callout"
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        self._segments: dict[str, Segmented] = {}
        for kind, shapes in SHAPES_FOR.items():
            seg = icon_segmented(shapes, SHAPE_LABELS, "shape")
            seg.changed.connect(lambda shape: self._emit(shape=shape))
            self._segments[kind] = seg
            col.addWidget(seg)
        self._corner_row = QWidget()
        row = QVBoxLayout(self._corner_row)
        row.setContentsMargins(0, 4, 0, 0)
        row.setSpacing(4)
        head = QHBoxLayout()
        label = QLabel("Corners")
        self._corner_value = QLabel()
        self._corner_value.setProperty("sfRole", "secondary")
        head.addWidget(label)
        head.addStretch(1)
        head.addWidget(self._corner_value)
        row.addLayout(head)
        self._corner = slider_factory(Qt.Horizontal)
        self._corner.setRange(*CORNER_RANGE)
        self._corner.setAccessibleName("Corner rounding")
        self._corner.valueChanged.connect(self._corner_moved)
        row.addWidget(self._corner)
        col.addWidget(self._corner_row)

    def load(self, kind: str, style: Style) -> None:
        self._loading = True
        self._kind = kind if kind in SHAPES_FOR else "callout"
        for name, seg in self._segments.items():
            seg.setVisible(name == self._kind)
        seg = self._segments[self._kind]
        shapes = SHAPES_FOR[self._kind]
        seg.set_value(style.shape if style.shape in shapes else shapes[0])
        boxy = kind == "rect" and style.shape in ("rounded", "rect")
        self._corner_row.setVisible(boxy)
        corner = style.corner if style.corner != USUAL_CORNER else (0 if style.shape == "rect" else USUAL_RECT_CORNER)
        self._corner.setValue(corner)
        self._corner_value.setText(f"{corner} px")
        self._loading = False

    def _corner_moved(self, value: int) -> None:
        self._corner_value.setText(f"{value} px")
        self._emit(corner=int(value))

    def _emit(self, **changes) -> None:
        if not self._loading:
            self.changed.emit(changes)

    def retheme(self) -> None:
        for kind, seg in self._segments.items():
            retheme_segmented(seg, SHAPES_FOR[kind])

    def segment(self, kind: str) -> Segmented:
        return self._segments[kind]

    def corner_slider(self):
        return self._corner


class ArrowControls(QWidget):
    """Where an arrow's heads are. (Open head and Curved are switches in Effects.)"""

    changed = Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._loading = False
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        self.heads = icon_segmented(ARROW_HEADS, HEAD_LABELS, "heads")
        self.heads.changed.connect(lambda heads: self._loading or self.changed.emit({"heads": heads}))
        col.addWidget(self.heads)

    def load(self, style: Style) -> None:
        self._loading = True
        self.heads.set_value(style.heads)
        self._loading = False

    def retheme(self) -> None:
        retheme_segmented(self.heads, ARROW_HEADS)


class StepControls(QWidget):
    """A step number's numbering (1 2 3, A B C, I II III), its badge, and the next number to drop."""

    changed = Signal(dict)
    next_number = Signal(int)  # what the next click with the Step tool drops

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._loading = False
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        self.numbering = Segmented([(key, NUMBERING_LABELS[key]) for key in NUMBERINGS], value="digits")
        for key in NUMBERINGS:
            b = self.numbering.button(key)
            b.setToolTip({"digits": "Numbers", "letters": "Letters", "roman": "Roman numerals"}[key])
            b.setAccessibleName(b.toolTip())
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setMinimumWidth(ICON_SEGMENT_MIN_WIDTH)  # the panel's width shares out, not the text's
        self.numbering.changed.connect(lambda key: self._emit(numbering=key))
        col.addWidget(self.numbering)
        self.badge = icon_segmented(BADGES, BADGE_LABELS, "badge")
        self.badge.changed.connect(lambda key: self._emit(badge=key))
        col.addWidget(self.badge)
        row = QHBoxLayout()
        row.addWidget(QLabel("Next number"))
        row.addStretch(1)
        self.next = FocusedSpinBox()
        self.next.setRange(1, 999)
        self.next.setToolTip("What the next click drops; the ones after follow on from it")
        screen_reader_name(self.next, "Next step number")
        self.next.valueChanged.connect(lambda v: self._loading or self.next_number.emit(int(v)))
        row.addWidget(self.next)
        col.addLayout(row)

    def load(self, style: Style, next_number: int) -> None:
        self._loading = True
        self.numbering.set_value(style.numbering)
        self.badge.set_value(style.badge)
        self.next.setValue(max(1, min(999, next_number)))
        self._loading = False

    def _emit(self, **changes) -> None:
        if not self._loading:
            self.changed.emit(changes)

    def retheme(self) -> None:
        retheme_segmented(self.badge, BADGES)


class TextFormatControls(QWidget):
    """Font, bold, italic, underline and alignment for text and callouts."""

    changed = Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._loading = False
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        self.font = FocusedFontBox()
        self.font.setToolTip("Font")
        screen_reader_name(self.font, "Font")
        self.font.setFocusPolicy(Qt.StrongFocus)
        self.font.currentFontChanged.connect(lambda f: self._emit(font_family=f.family()))
        col.addWidget(self.font)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.toggles: dict[str, QToolButton] = {}
        for key, tip in (("bold", "Bold"), ("italic", "Italic"), ("underline", "Underline")):
            b = QToolButton()
            b.setObjectName("FormatToggle")
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setAccessibleName(tip)
            b.setIconSize(QSize(18, 18))
            b.setFixedSize(30, 30)
            b.setCursor(Qt.PointingHandCursor)
            b.toggled.connect(lambda on, k=key: self._emit(**{k: bool(on)}))
            self.toggles[key] = b
            row.addWidget(b)
        row.addSpacing(6)
        self.align = icon_segmented(("left", "center", "right"), ALIGN_LABELS, "align")
        self.align.changed.connect(lambda key: self._emit(align=key))
        row.addWidget(self.align, 1)
        col.addLayout(row)

    def load(self, kind: str, style: Style) -> None:
        self._loading = True
        if style.font_family:
            self.font.setCurrentFont(QFont(style.font_family))
        else:
            from ..fonts import ui_font
            self.font.setCurrentFont(ui_font(10))
        self.toggles["bold"].setVisible(kind == "text")  # callouts are always demi-bold
        for key, b in self.toggles.items():
            b.setChecked(bool(getattr(style, key)))
        self.align.set_value(text_align(kind, style))
        self._loading = False

    def _emit(self, **changes) -> None:
        if not self._loading:
            self.changed.emit(changes)

    def retheme(self) -> None:
        retheme_segmented(self.align, ("left", "center", "right"))
        for key, b in self.toggles.items():
            b.setIcon(icon(key))


class DimControls(QWidget):
    """How dark a spotlight makes the rest of the snip."""

    changed = Signal(dict)

    def __init__(self, slider_factory, parent=None) -> None:
        super().__init__(parent)
        self._loading = False
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        head = QHBoxLayout()
        label = QLabel("DIM")
        label.setProperty("sfRole", "section")
        self._value = QLabel()
        self._value.setProperty("sfRole", "secondary")
        head.addWidget(label)
        head.addStretch(1)
        head.addWidget(self._value)
        col.addLayout(head)
        self.slider = slider_factory(Qt.Horizontal)
        self.slider.setRange(*DIM_RANGE)
        self.slider.setAccessibleName("Dim the rest of the snip")
        self.slider.valueChanged.connect(self._moved)
        col.addWidget(self.slider)

    def load(self, style: Style) -> None:
        self._loading = True
        dim = style.dim if style.dim != USUAL_DIM else round(150 * 100 / 255)
        self.slider.setValue(dim)
        self._value.setText(f"{dim}%")
        self._loading = False

    def _moved(self, value: int) -> None:
        self._value.setText(f"{value}%")
        if not self._loading:
            self.changed.emit({"dim": int(value)})
