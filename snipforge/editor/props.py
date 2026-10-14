"""Contextual property bar: shows and edits the style of the selection or active tool."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSlider,
    QSpinBox,
    QToolButton,
    QWidget,
    QWidgetAction,
)

from ..icons import icon
from .stamps import STAMPS, stamp_icon
from .style import CALLOUT_SHAPES, PALETTE, STRENGTH_KINDS, STYLE_FIELDS, Style

TOOL_TITLES = {
    "select": "Select", "arrow": "Arrow", "line": "Line", "rect": "Rectangle", "ellipse": "Ellipse",
    "pen": "Pen", "highlight": "Highlighter", "text": "Text", "callout": "Callout", "stamp": "Stamp",
    "step": "Step", "pixelate": "Pixelate", "blur": "Blur", "spotlight": "Spotlight", "crop": "Crop",
}
HINTS = {
    "select": "Click to select  •  drag to move  •  Ctrl+click adds  •  Ctrl+D duplicate",
    "crop": "Drag edges in to cut, back out to restore  •  drag inside to move  •  Enter applies, Esc cancels",
    "spotlight": "Drag the area to keep lit",
    "text": "Click to type  •  Esc to finish",
    "callout": "Drag a box, then type  •  drag the orange dot to aim the pointer",
    "stamp": "Click to drop  •  drag to size",
    "step": "Click to drop the next number",
}
SHAPE_TIPS = {"rounded": "Rounded box", "rect": "Box", "ellipse": "Ellipse", "pill": "Pill"}
STAMP_COLUMNS = 6


class Swatch(QPushButton):
    def __init__(self, color: str) -> None:
        super().__init__()
        self.color = color
        self.setFixedSize(20, 20)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(color)
        self.setStyleSheet(
            f"QPushButton {{ background: {color}; border: 1px solid #555; border-radius: 10px; }}"
            "QPushButton:checked { border: 2px solid #ffffff; }")


class PropertyBar(QWidget):
    changed = Signal(dict)  # style field -> new value
    crop_action = Signal(str)  # apply | cancel | reset

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PropertyBar")
        self._loading = False
        self._style_color = ""
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(8)

        self._title = QLabel()
        self._title.setObjectName("PropTitle")
        row.addWidget(self._title)

        row.addWidget(self._build_shapes())
        self._tail = QCheckBox("Pointer")
        self._tail.setToolTip("Show the speech-bubble pointer")
        self._tail.toggled.connect(lambda on: self._emit(tail=on))
        row.addWidget(self._tail)

        self._stamp_button = self._build_stamp_picker()
        row.addWidget(self._stamp_button)

        row.addWidget(self._build_colors())

        self._width_label = QLabel("Size")
        self._width = QSlider(Qt.Horizontal)
        self._width.setRange(1, 20)
        self._width.setFixedWidth(100)
        self._width.valueChanged.connect(lambda v: self._emit(width=float(v)))
        self._width_value = QLabel()
        self._width_value.setFixedWidth(22)
        for w in (self._width_label, self._width, self._width_value):
            row.addWidget(w)

        self._font_label = QLabel("Font")
        self._font = QSpinBox()
        self._font.setRange(8, 96)
        self._font.valueChanged.connect(lambda v: self._emit(font_size=int(v)))
        row.addWidget(self._font_label)
        row.addWidget(self._font)

        self._dash = QCheckBox("Dashed")
        self._dash.toggled.connect(lambda on: self._emit(dash=on))
        self._fill = QCheckBox("Fill")
        self._fill.toggled.connect(lambda on: self._emit(fill=on))
        self._shadow = QCheckBox("Shadow")
        self._shadow.toggled.connect(lambda on: self._emit(shadow=on))
        for w in (self._dash, self._fill, self._shadow):
            row.addWidget(w)

        self._crop_box = QWidget()
        crop_row = QHBoxLayout(self._crop_box)
        crop_row.setContentsMargins(0, 0, 0, 0)
        crop_row.setSpacing(6)
        for label, action, tip, primary in (
                ("Apply", "apply", "Keep this crop (Enter)", True),
                ("Cancel", "cancel", "Discard crop changes (Esc)", False),
                ("Show all", "reset", "Bring back the whole screenshot", False)):
            b = QPushButton(label)
            b.setObjectName("CropPrimary" if primary else "CropButton")
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, a=action: self.crop_action.emit(a))
            crop_row.addWidget(b)
        row.addWidget(self._crop_box)

        row.addStretch(1)
        self._hint = QLabel()
        self._hint.setObjectName("PropHint")
        row.addWidget(self._hint)
        # Clicking controls must not steal keyboard focus from the canvas (tool keys, Delete...).
        for w in self.findChildren(QWidget):
            if not isinstance(w, QSpinBox) and w.parent() is not None:
                w.setFocusPolicy(Qt.NoFocus)

    # ------------------------------------------------------------------ builders
    def _build_shapes(self) -> QWidget:
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 4, 0)
        lay.setSpacing(2)
        self._shape_group = QButtonGroup(self)
        self._shape_buttons: dict[str, QToolButton] = {}
        for shape in CALLOUT_SHAPES:
            b = QToolButton()
            b.setObjectName("ShapeButton")
            b.setIcon(icon(f"shape_{shape}"))
            b.setIconSize(QSize(18, 18))
            b.setCheckable(True)
            b.setToolTip(SHAPE_TIPS[shape])
            b.clicked.connect(lambda _=False, s=shape: self._emit(shape=s))
            self._shape_group.addButton(b)
            self._shape_buttons[shape] = b
            lay.addWidget(b)
        self._shapes_box = box
        return box

    def _build_stamp_picker(self) -> QToolButton:
        button = QToolButton()
        button.setObjectName("StampPicker")
        button.setIconSize(QSize(22, 22))
        button.setText("Choose")
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(button)
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(4)
        for i, (name, (label, _svg)) in enumerate(STAMPS.items()):
            b = QToolButton()
            b.setObjectName("StampChoice")
            b.setIcon(stamp_icon(name, 64))
            b.setIconSize(QSize(32, 32))
            b.setToolTip(label)
            b.setAutoRaise(True)
            b.clicked.connect(lambda _=False, n=name: (menu.close(), self._emit(stamp=n)))
            grid.addWidget(b, i // STAMP_COLUMNS, i % STAMP_COLUMNS)
        action = QWidgetAction(menu)
        action.setDefaultWidget(grid_host)
        menu.addAction(action)
        button.setMenu(menu)
        return button

    def _build_colors(self) -> QWidget:
        self._swatches = [Swatch(c) for c in PALETTE]
        self._color_box = QWidget()
        swatch_row = QHBoxLayout(self._color_box)
        swatch_row.setContentsMargins(6, 0, 6, 0)
        swatch_row.setSpacing(5)
        for swatch in self._swatches:
            swatch.clicked.connect(lambda _=False, s=swatch: self._emit(color=s.color))
            swatch_row.addWidget(swatch)
        self._custom = QPushButton("…")
        self._custom.setObjectName("CustomColor")
        self._custom.setFixedSize(26, 20)
        self._custom.setToolTip("Custom color")
        self._custom.clicked.connect(self._pick_custom)
        swatch_row.addWidget(self._custom)
        return self._color_box

    # ------------------------------------------------------------------ state
    def load(self, kind: str, style: Style | None, has_selection: bool) -> None:
        self._loading = True
        fields = STYLE_FIELDS.get(kind, ()) if style else ()
        title = TOOL_TITLES.get(kind, kind.title())
        self._title.setText(f"{title}  •  selected" if has_selection else title)
        self._shapes_box.setVisible("shape" in fields)
        self._tail.setVisible("tail" in fields)
        self._stamp_button.setVisible("stamp" in fields)
        self._color_box.setVisible("color" in fields)
        width_on = "width" in fields
        for w in (self._width_label, self._width, self._width_value):
            w.setVisible(width_on)
        self._width_label.setText({"callout": "Border"}.get(kind, "Strength" if kind in STRENGTH_KINDS else "Size"))
        self._font_label.setVisible("font_size" in fields)
        self._font.setVisible("font_size" in fields)
        self._dash.setVisible("dash" in fields)
        self._fill.setVisible("fill" in fields)
        self._fill.setText({"text": "Background", "callout": "Filled"}.get(kind, "Fill"))
        self._shadow.setVisible("shadow" in fields)
        self._crop_box.setVisible(kind == "crop")
        self._hint.setText("" if has_selection else HINTS.get(kind, ""))
        if style:
            self._style_color = style.color
            for swatch in self._swatches:
                swatch.setChecked(swatch.color.lower() == style.color.lower())
            self._width.setValue(int(round(style.width)))
            self._width_value.setText(str(int(round(style.width))))
            self._font.setValue(int(style.font_size))
            self._dash.setChecked(style.dash)
            self._fill.setChecked(style.fill)
            self._shadow.setChecked(style.shadow)
            self._tail.setChecked(style.tail)
            if style.shape in self._shape_buttons:
                self._shape_buttons[style.shape].setChecked(True)
            self._stamp_button.setIcon(stamp_icon(style.stamp if style.stamp in STAMPS else "check", 64))
        self._loading = False

    def _emit(self, **changes) -> None:
        if "width" in changes:
            self._width_value.setText(str(int(changes["width"])))
        if "stamp" in changes:
            self._stamp_button.setIcon(stamp_icon(changes["stamp"], 64))
        if not self._loading:
            self.changed.emit(changes)

    def _pick_custom(self) -> None:
        color = QColorDialog.getColor(QColor(self._style_color or "#ff3b30"), self, "Annotation color")
        if color.isValid():
            self._emit(color=color.name())
