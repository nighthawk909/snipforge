"""Properties panel on the right of the editor: shows and edits the style of the selection or the
active tool, with only the sections that tool uses."""
from __future__ import annotations

import sys

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QColorDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..icons import icon
from ..widgets import Segmented, ToggleSwitch
from ..wording import keys
from .edge_controls import CutoutControls, EdgeControls
from .quick_tiles import QuickStyles, QuickStyleTile, SavedStyleTile
from .stamps import STAMPS, stamp_icon
from .style import CALLOUT_SHAPES, COLOR_NAMES, PALETTE, STRENGTH_KINDS, STYLE_FIELDS, Style

PANEL_WIDTH = 288
TITLES = {
    "select": "Select", "arrow": "Arrow", "line": "Line", "rect": "Rectangle", "ellipse": "Ellipse",
    "pen": "Pen", "highlight": "Highlighter", "text": "Text", "callout": "Callout", "stamp": "Stamp",
    "step": "Step number", "pixelate": "Pixelate", "blur": "Blur", "spotlight": "Spotlight", "crop": "Crop",
    "magnify": "Magnify", "cutout": "Cut out", "erase": "Smart erase",
}
DRAW_HINT = "Drag to draw. Drag markup that's already there to move it, or hold {alt} to draw over it."
HINTS = {
    "select": f"Click any markup to change it. Drag to move, {keys('Ctrl+click')} to add to the selection, "
              f"Delete to remove, {keys('Ctrl+D')} to duplicate. Turn, flip or resize the snip under Image.",
    **dict.fromkeys(("arrow", "line", "rect", "ellipse", "pen"), DRAW_HINT),
    "crop": "Drag the edges in to cut and back out to restore. Drag inside to move. Enter applies, Esc cancels.",
    "spotlight": "Drag over the area to keep lit. Everything else dims.",
    "cutout": "Drag across the part to take out. What's left is joined together.",
    "magnify": "Drag over what to enlarge. Drag the lens to move it, or inside the frame to show another area.",
    "text": "Click to type. Esc finishes. Drag text to move it.",
    "callout": "Drag a box, then type. Drag the pointer (or its orange dot) to aim it. Drag a callout to move it.",
    "stamp": "Click to drop a stamp, or drag to size it. App name and date adds where and when you took the snip.",
    "step": "Click to drop the next number.",
    "blur": "Drag over anything private. Redact in the toolbar finds emails, phone and card numbers for you.",
    "pixelate": "Drag over anything private.",
    "erase": "Drag over something to remove it. The area is filled in from what's around it.",
}
SHAPE_LABELS = {"rounded": "Rounded box", "rect": "Box", "ellipse": "Ellipse", "pill": "Pill"}
SWITCHES = ("tail", "fill", "dash", "shadow")
STAMP_COLUMNS = 6
EDGE_SWATCHES = {"#ffffff", "#1c1c1e"}  # need an outline to show against the panel
# action, icon, tooltip: the snip's own buttons in the Image section
IMAGE_ACTIONS = (
    ("rotate_left", "rotate_left", f"Rotate left ({keys('Ctrl+Shift+R')})"),
    ("rotate_right", "rotate_right", f"Rotate right ({keys('Ctrl+R')})"),
    ("flip_horizontal", "flip", "Flip horizontal"),
    ("flip_vertical", "flip_vertical", "Flip vertical"),
)


def alt_key() -> str:
    """What the Alt key is called here: Option on a Mac."""
    return "Option" if sys.platform == "darwin" else "Alt"


class Swatch(QPushButton):
    """Round color button; draws itself so the selection ring follows the theme."""

    def __init__(self, color: str) -> None:
        super().__init__()
        self.color = color
        self.setFixedSize(24, 24)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        name = COLOR_NAMES.get(color.lower(), color)
        self.setToolTip(name)
        self.setAccessibleName(f"Color: {name}")

    def paintEvent(self, _e) -> None:
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.isChecked():
            p.setPen(QPen(QColor(t.accent.fill), 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(1, 1, 22, 22))
        p.setPen(QPen(QColor(t.palette.text3 if self.color.lower() in EDGE_SWATCHES else self.color), 1))
        p.setBrush(QColor(self.color))
        p.drawEllipse(QRectF(4.5, 4.5, 15, 15))
        p.end()


class NoWheelSlider(QSlider):
    """The wheel scrolls the panel instead of silently changing the size."""

    def wheelEvent(self, e) -> None:  # noqa: N802 - Qt API
        e.ignore()


class PropertiesPanel(QScrollArea):
    changed = Signal(dict)          # style field -> new value
    crop_action = Signal(str)       # apply | cancel | reset
    image_action = Signal(str)      # rotate_left | rotate_right | flip_horizontal | flip_vertical | resize
    pick_color = Signal()           # the eyedropper button: start (or stop) picking a color from the snip
    style_saved = Signal(str)       # the selection's look was saved as a quick style for this tool
    edges_changed = Signal(object)  # Edges: the snip's border and torn sides
    source_stamp = Signal()         # add the "App name and date" text stamp
    cutout_changed = Signal(dict)   # the Cut out tool's direction or seam
    editing_done = Signal()         # finished typing in a field: give the keyboard back to the canvas

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PropertiesPanel")
        self.setFixedWidth(PANEL_WIDTH)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)  # never hide controls at large text sizes
        # Clicking the panel must not take the keyboard from the canvas (tool keys, Delete, Esc...).
        self.setFocusPolicy(Qt.NoFocus)
        self.viewport().setFocusPolicy(Qt.NoFocus)
        self._loading = False
        self._kind = "select"
        self._style_color = ""
        self._sections: dict[str, QWidget] = {}
        self._switches: dict[str, ToggleSwitch] = {}

        body = QWidget()
        body.setObjectName("PanelBody")
        self._column = QVBoxLayout(body)
        self._column.setContentsMargins(0, 0, 0, 16)
        self._column.setSpacing(0)
        self._build_header()
        self._build_quick()
        self._build_shape()
        self._build_stamps()
        self._build_colors()
        self._build_size()
        self._build_text()
        self._build_effects()
        self._build_crop()
        self._build_cutout()
        self._build_image()
        self._build_edges()
        self._column.addStretch(1)
        self.setWidget(body)
        # Controls are reachable with Tab but don't take the keyboard when clicked; you can type a font size.
        for w in body.findChildren(QWidget):
            w.setFocusPolicy(Qt.TabFocus if isinstance(w, (QAbstractButton, QSlider)) else Qt.NoFocus)
        self._font.setFocusPolicy(Qt.StrongFocus)
        self._font.lineEdit().setFocusPolicy(Qt.StrongFocus)
        self._font.editingFinished.connect(self.editing_done)
        self.retheme()

    # ------------------------------------------------------------------ builders
    def _section(self, name: str, title: str | None) -> QVBoxLayout:
        box = QWidget()
        box.setObjectName("PanelSection")
        box.setAttribute(Qt.WA_StyledBackground, True)
        col = QVBoxLayout(box)
        col.setContentsMargins(16, 12, 16, 12)
        col.setSpacing(8)
        if title:
            label = QLabel(title.upper())
            label.setProperty("sfRole", "section")
            col.addWidget(label)
        self._sections[name] = box
        self._column.addWidget(box)
        return col

    def _build_header(self) -> None:
        head = QWidget()
        col = QVBoxLayout(head)
        col.setContentsMargins(16, 14, 16, 8)
        col.setSpacing(6)
        row = QHBoxLayout()
        self._title = QLabel()
        self._title.setProperty("sfRole", "heading")
        row.addWidget(self._title)
        row.addStretch(1)
        self._chip = QLabel("Selected")
        self._chip.setObjectName("Chip")
        row.addWidget(self._chip)
        col.addLayout(row)
        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setProperty("sfRole", "secondary")
        col.addWidget(self._hint)
        self._column.addWidget(head)

    def _build_quick(self) -> None:
        col = self._section("quick", "Quick styles")
        self._quick = QuickStyles()
        self._quick.apply.connect(lambda changes: self._emit(**changes))
        self._quick.saved.connect(self.style_saved)
        self._tiles = self._quick.tiles
        col.addWidget(self._quick)

    def _build_shape(self) -> None:
        col = self._section("shape", "Shape")
        self._shapes = Segmented([(s, SHAPE_LABELS[s]) for s in CALLOUT_SHAPES], value=CALLOUT_SHAPES[0])
        for shape in CALLOUT_SHAPES:
            b = self._shapes.button(shape)
            b.setToolTip(SHAPE_LABELS[shape])
            b.setAccessibleName(SHAPE_LABELS[shape])
            b.setText("")  # icon only; the name stays in the tooltip and for screen readers
            b.setToolButtonStyle(Qt.ToolButtonIconOnly)
            b.setIconSize(QSize(20, 20))
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._shapes.changed.connect(lambda shape: self._emit(shape=shape))
        col.addWidget(self._shapes)

    def _build_stamps(self) -> None:
        col = self._section("stamp", "Stamp")
        grid = QGridLayout()
        grid.setSpacing(4)
        self._stamp_group = QButtonGroup(self)
        self._stamp_buttons: dict[str, QToolButton] = {}
        for i, (name, (label, _svg)) in enumerate(STAMPS.items()):
            b = QToolButton()
            b.setObjectName("StampChoice")
            b.setIcon(stamp_icon(name, 64))
            b.setIconSize(QSize(28, 28))
            b.setCheckable(True)
            b.setToolTip(label)
            b.setAccessibleName(label)
            b.clicked.connect(lambda _=False, n=name: self._emit(stamp=n))
            self._stamp_group.addButton(b)
            self._stamp_buttons[name] = b
            grid.addWidget(b, i // STAMP_COLUMNS, i % STAMP_COLUMNS)
        col.addLayout(grid)
        self._source_stamp = QPushButton("App name and date")
        self._source_stamp.setAccessibleName("Add an app name and date stamp")
        self._source_stamp.clicked.connect(self.source_stamp)
        col.addWidget(self._source_stamp)
        self.set_source_stamp_available(True)

    def _build_colors(self) -> None:
        col = self._section("color", None)
        head = QHBoxLayout()
        head.setSpacing(4)
        label = QLabel("COLOR")
        label.setProperty("sfRole", "section")
        head.addWidget(label)
        head.addStretch(1)
        self._custom = QToolButton()
        self._custom.setObjectName("CustomColor")
        self._custom.setToolTip("Custom color")
        self._custom.setAccessibleName("Custom color")
        self._custom.clicked.connect(self._pick_custom)
        head.addWidget(self._custom)
        self._picker = QToolButton()
        self._picker.setObjectName("PickColor")
        self._picker.setCheckable(True)
        self._picker.setToolTip("Pick a color from the snip (Esc cancels)")
        self._picker.setAccessibleName("Pick a color from the snip")
        self._picker.clicked.connect(self._pick_clicked)
        head.addWidget(self._picker)
        col.addLayout(head)
        row = QHBoxLayout()
        row.setSpacing(2)
        self._swatches = {c: Swatch(c) for c in PALETTE}
        for color, swatch in self._swatches.items():  # checked by hand: a custom color clears every ring
            swatch.clicked.connect(lambda _=False, c=color: self._pick_swatch(c))
            row.addWidget(swatch)
        row.addStretch(1)
        col.addLayout(row)

    def _build_size(self) -> None:
        col = self._section("size", None)
        head = QHBoxLayout()
        self._size_label = QLabel()
        self._size_label.setProperty("sfRole", "section")
        self._size_value = QLabel()
        self._size_value.setProperty("sfRole", "secondary")
        head.addWidget(self._size_label)
        head.addStretch(1)
        head.addWidget(self._size_value)
        col.addLayout(head)
        self._slider = NoWheelSlider(Qt.Horizontal)
        self._slider.setRange(1, 20)
        self._slider.valueChanged.connect(lambda v: self._emit(width=float(v)))
        col.addWidget(self._slider)

    def _build_text(self) -> None:
        col = self._section("text", "Text")
        row = QHBoxLayout()
        label = QLabel("Font size")
        row.addWidget(label)
        row.addStretch(1)
        self._font = QSpinBox()
        self._font.setRange(8, 96)
        self._font.setAccessibleName("Font size")
        self._font.valueChanged.connect(lambda v: self._emit(font_size=int(v)))
        row.addWidget(self._font)
        col.addLayout(row)

    def _switch_row(self, col: QVBoxLayout, key: str, label: str) -> ToggleSwitch:
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 2, 0, 2)
        text = QLabel(label)
        row.addWidget(text, 1)
        switch = ToggleSwitch(label)
        row.addWidget(switch)
        switch.setProperty("rowLabel", text)
        self._switches[key] = switch
        col.addWidget(row_widget)
        return switch

    def _build_effects(self) -> None:
        col = self._section("effects", "Effects")
        for key, label in (("tail", "Pointer"), ("fill", "Fill"), ("dash", "Dashed line"), ("shadow", "Shadow")):
            switch = self._switch_row(col, key, label)
            switch.toggled.connect(lambda on, k=key: self._emit(**{k: on}))

    def _build_crop(self) -> None:
        col = self._section("crop", "Crop")
        self._crop_buttons: dict[str, QPushButton] = {}
        row = QHBoxLayout()
        for label, action, tip, primary in (
                ("Apply", "apply", "Keep this crop (Enter)", True),
                ("Cancel", "cancel", "Discard crop changes (Esc)", False)):
            b = QPushButton(label)
            b.setToolTip(tip)
            if primary:
                b.setProperty("variant", "primary")
            b.clicked.connect(lambda _=False, a=action: self.crop_action.emit(a))
            self._crop_buttons[action] = b
            row.addWidget(b, 1)
        col.addLayout(row)
        reset = QPushButton("Show the whole snip")
        reset.setToolTip("Bring back everything you cropped away")
        reset.clicked.connect(lambda: self.crop_action.emit("reset"))
        self._crop_buttons["reset"] = reset
        col.addWidget(reset)

    def _build_cutout(self) -> None:
        col = self._section("cutout", "Cut out")
        self._cutout = CutoutControls()
        self._cutout.changed.connect(self.cutout_changed)
        col.addWidget(self._cutout)

    def _build_edges(self) -> None:
        col = self._section("edges", "Edges")
        self._edges = EdgeControls(Swatch)
        self._edges.changed.connect(self.edges_changed)
        col.addWidget(self._edges)

    def _build_image(self) -> None:
        col = self._section("image", "Image")
        self._image_size = QLabel()
        self._image_size.setProperty("sfRole", "secondary")
        col.addWidget(self._image_size)
        row = QHBoxLayout()
        row.setSpacing(6)
        self._image_buttons: dict[str, QAbstractButton] = {}
        for action, _icon, tip in IMAGE_ACTIONS:
            b = QToolButton()
            b.setObjectName("ImageAction")
            b.setToolTip(tip)
            b.setAccessibleName(tip.split(" (")[0])
            b.setIconSize(QSize(18, 18))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, a=action: self.image_action.emit(a))
            self._image_buttons[action] = b
            row.addWidget(b)
        row.addStretch(1)
        resize = QPushButton("Resize…")
        resize.setToolTip("Change the snip's size in pixels or percent")
        resize.setAccessibleName("Resize")
        resize.clicked.connect(lambda: self.image_action.emit("resize"))
        self._image_buttons["resize"] = resize
        row.addWidget(resize)
        col.addLayout(row)

    # ------------------------------------------------------------------ state
    def load(self, kind: str, style: Style | None, has_selection: bool) -> None:
        self._loading = True
        self._kind = kind
        fields = STYLE_FIELDS.get(kind, ()) if style else ()
        self._title.setText(TITLES.get(kind, kind.title()))
        self._chip.setVisible(has_selection)
        self._hint.setText("" if has_selection else HINTS.get(kind, "").format(alt=alt_key()))
        self._hint.setVisible(bool(self._hint.text()))
        show = {
            "quick": bool(fields) and self._quick.worth_showing(kind, has_selection), "shape": "shape" in fields,
            "stamp": "stamp" in fields,
            "color": "color" in fields, "size": "width" in fields, "text": "font_size" in fields,
            "effects": any(f in fields for f in SWITCHES), "crop": kind == "crop", "image": kind == "select",
            "cutout": kind == "cutout", "edges": kind == "select",
        }
        for name, box in self._sections.items():
            box.setVisible(show[name])
        for key in SWITCHES:
            self._switches[key].parentWidget().setVisible(key in fields)
        self._switches["fill"].property("rowLabel").setText(
            {"text": "Background", "callout": "Filled"}.get(kind, "Fill"))
        self._size_label.setText(self.size_label().upper())
        self._slider.setAccessibleName(self.size_label())
        if style:
            self._load_style(style)
        self._quick.load(kind, style, has_selection)
        self._loading = False

    def _load_style(self, style: Style) -> None:
        self._style_color = style.color
        for color, swatch in self._swatches.items():
            swatch.setChecked(color.lower() == style.color.lower())
        self._slider.setValue(int(round(style.width)))
        self._size_value.setText(f"{int(round(style.width))}")
        self._font.setValue(int(style.font_size))
        for key in SWITCHES:
            self._switches[key].setChecked(bool(getattr(style, key)))
        if style.shape in CALLOUT_SHAPES:
            self._shapes.set_value(style.shape)
        if style.stamp in self._stamp_buttons:
            self._stamp_buttons[style.stamp].setChecked(True)

    def set_source_stamp_available(self, available: bool) -> None:
        self._source_stamp.setEnabled(available)
        why = ("Add text saying which app or window this snip came from, and when you took it" if available else
               "Only for snips in your library: they remember where and when they were taken")
        self._source_stamp.setToolTip(why)
        self._source_stamp.setAccessibleDescription(why)

    def source_stamp_button(self) -> QPushButton:
        return self._source_stamp

    def load_edges(self, edges) -> None:
        self._edges.load(edges)

    def load_cutout(self, options: dict) -> None:
        self._cutout.load(options)

    def edge_controls(self) -> EdgeControls:
        return self._edges

    def cutout_controls(self) -> CutoutControls:
        return self._cutout

    def set_image_size(self, width: int, height: int) -> None:
        self._image_size.setText(f"{width} × {height} px")

    def retheme(self) -> None:
        for shape in CALLOUT_SHAPES:
            self._shapes.button(shape).setIcon(icon(f"shape_{shape}"))
        self._custom.setIcon(icon("plus"))
        self._cutout.retheme()
        self._picker.setIcon(icon("pipette"))
        for action, icon_name, _tip in IMAGE_ACTIONS:
            self._image_buttons[action].setIcon(icon(icon_name))
        self.viewport().update()
        for w in (*self._swatches.values(), *self._tiles, *self._quick.saved_tiles, *self._switches.values()):
            w.update()

    def _pick_swatch(self, color: str) -> None:
        for c, swatch in self._swatches.items():
            swatch.setChecked(c == color)
        self._emit(color=color)

    def _emit(self, **changes) -> None:
        if "width" in changes:
            self._size_value.setText(str(int(changes["width"])))
        if changes and not self._loading:
            self.changed.emit(changes)

    def _pick_clicked(self) -> None:
        # Undo the click's own toggle: the button follows the canvas (set_picking), which says
        # whether picking really started or ended.
        self._picker.setChecked(not self._picker.isChecked())
        self.pick_color.emit()

    def set_picking(self, on: bool) -> None:
        self._picker.setChecked(on)

    def _pick_custom(self) -> None:
        color = QColorDialog.getColor(QColor(self._style_color or "#ff3b30"), self, "Annotation color")
        if color.isValid():
            self._emit(color=color.name())

    # ------------------------------------------------------------------ for tests and callers
    def title(self) -> str:
        return self._title.text()

    def chip_visible(self) -> bool:
        return not self._chip.isHidden()

    def section(self, name: str) -> QWidget:
        return self._sections[name]

    def size_label(self) -> str:
        return {"callout": "Border", "magnify": "Border"}.get(
            self._kind, "Strength" if self._kind in STRENGTH_KINDS else "Size")

    def switch(self, key: str) -> ToggleSwitch:
        return self._switches[key]

    def slider(self) -> QSlider:
        return self._slider

    def swatch(self, color: str) -> Swatch:
        return self._swatches[color]

    def quick_tile(self, index: int) -> QuickStyleTile:
        return self._tiles[index]

    def saved_tile(self, index: int) -> SavedStyleTile:
        return self._quick.saved_tiles[index]

    def saved_count(self) -> int:
        return len(self._quick.saved_tiles)

    def save_button(self) -> QPushButton:
        return self._quick.save_button

    def crop_button(self, action: str) -> QPushButton:
        return self._crop_buttons[action]

    def stamp_button(self, name: str) -> QToolButton:
        return self._stamp_buttons[name]

    def pick_button(self) -> QToolButton:
        return self._picker

    def image_button(self, action: str) -> QAbstractButton:
        return self._image_buttons[action]

    def image_size_text(self) -> str:
        return self._image_size.text()
