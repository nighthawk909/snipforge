"""Properties panel on the right of the editor: shows and edits the style of the selection or the
active tool, with only the sections that tool uses."""
from __future__ import annotations

from pathlib import Path

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
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..icons import icon
from ..widgets import Segmented, ToggleSwitch, screen_reader_name
from ..wording import keys
from . import colors, custom_stamps
from .edge_controls import CutoutControls, EdgeControls
from .fill_controls import FillControls
from .layers import LayersList
from .quick_tiles import QuickStyles, QuickStyleTile, SavedStyleTile
from .stamps import STAMPS, stamp_icon
from .style import COLOR_NAMES, STRENGTH_KINDS, STYLE_FIELDS, Style, line_style
from .text_color_controls import TextColorControls
from .variant_controls import (
    ArrowControls,
    DimControls,
    FocusedSpinBox,
    ShapeControls,
    StepControls,
    TextFormatControls,
)

PANEL_WIDTH = 288
TITLES = {
    "select": "Select", "arrow": "Arrow", "line": "Line", "rect": "Rectangle", "ellipse": "Ellipse",
    "pen": "Pen", "highlight": "Highlighter", "highlight_box": "Highlighter", "text": "Text", "callout": "Callout",
    "stamp": "Stamp",
    "step": "Step number", "pixelate": "Pixelate", "blur": "Blur", "spotlight": "Spotlight", "crop": "Crop",
    "magnify": "Magnify", "cutout": "Cut out", "erase": "Smart erase", "move": "Smart Move", "piece": "Moved piece",
}
DRAW_HINT = "Drag to draw. Drag markup that's already there to move it, or hold {alt} to draw over it."
HINTS = {
    "select": f"Click any markup to change it. Drag to move, {keys('Ctrl+click')} to add to the selection, "
              f"Delete to remove, {keys('Ctrl+D')} to duplicate, {keys('Ctrl+C')} and {keys('Ctrl+V')} to copy "
              "and paste. Right-click for more. Turn, flip or resize the snip under Image.",
    **dict.fromkeys(("line", "ellipse", "pen"), DRAW_HINT),
    "arrow": DRAW_HINT + " Heads go at either end, or both; Curved adds a bend you drag.",
    "rect": DRAW_HINT + " Shape makes it a box, a diamond, a triangle or a star.",
    "crop": "Drag the edges in to cut and back out to restore. Drag inside to move. Enter applies, Esc cancels.",
    "spotlight": "Drag over the area to keep lit. Everything else dims: choose how much, and the lit shape.",
    "cutout": "Drag across the part to take out. What's left is joined together.",
    "magnify": "Drag over what to enlarge. Drag the lens to move it, or inside the frame to show another area.",
    "text": "Click to type. Esc finishes. Drag text to move it. Outline makes it read on any background.",
    "callout": "Drag a box, then type. Drag the pointer (or its orange dot) to aim it. Drag a callout to move it.",
    "stamp": "Click to drop a stamp, or drag to size it. Add your own pictures, too. App name and date adds where "
             "and when you took the snip.",
    "step": "Click to drop the next number: 1 2 3, A B C or I II III, in a circle, square or pill.",
    "blur": "Drag over anything private. Redact in the toolbar finds emails, phone and card numbers for you.",
    "pixelate": "Drag over anything private.",
    "erase": "Drag over something to remove it. The area is filled in from what's around it.",
    "move": "Drag over the part to move. Then drag the piece where you want it: where it was is filled in from "
            "what's around it.",
}
SWITCHES = ("tail", "open_head", "curve", "box", "fill", "rounded", "outline", "shadow")
SWITCH_LABELS = {"tail": "Pointer", "open_head": "Open head", "curve": "Curved", "box": "Box, not a stroke",
                 "fill": "Fill", "rounded": "Rounded corners", "outline": "Outline", "shadow": "Shadow"}
LINE_STYLES = (("solid", "Solid"), ("dashed", "Dashed"), ("dotted", "Dotted"))
LINE_CHANGES = {"solid": {"dash": False, "dotted": False}, "dashed": {"dash": True, "dotted": False},
                "dotted": {"dash": True, "dotted": True}}  # dotted keeps dash: older versions draw dashes
STAMP_COLUMNS = 6
EDGE_SWATCHES = {"#ffffff", "#1c1c1e"}  # need an outline to show against the panel
ARRANGE_ACTIONS = {"left": "left", "center": "center", "right": "right", "top": "top", "middle": "middle",
                   "bottom": "bottom", "h": "distribute_horizontal", "v": "distribute_vertical"}
ARRANGE_TIPS = {"left": "Align left edges", "center": "Align centers", "right": "Align right edges",
                "top": "Align top edges", "middle": "Align middles", "bottom": "Align bottom edges",
                "h": "Space evenly left to right (three or more)", "v": "Space evenly top to bottom (three or more)"}
ARRANGE_ICONS = {"left": "align_left", "center": "align_center", "right": "align_right", "top": "align_top",
                 "middle": "align_middle", "bottom": "align_bottom", "h": "distribute_h", "v": "distribute_v"}
# action, icon, tooltip: the snip's own buttons in the Image section
IMAGE_ACTIONS = (
    ("rotate_left", "rotate_left", f"Rotate left ({keys('Ctrl+Shift+R')})"),
    ("rotate_right", "rotate_right", f"Rotate right ({keys('Ctrl+R')})"),
    ("flip_horizontal", "flip", "Flip horizontal"),
    ("flip_vertical", "flip_vertical", "Flip vertical"),
)


def alt_key() -> str:
    """What the Alt key is called."""
    return "Alt"


class Swatch(QPushButton):
    """Round color button; draws itself so the selection ring follows the theme."""

    def __init__(self, color: str) -> None:
        super().__init__()
        self.color = color
        self.setFixedSize(24, 24)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.set_color(color)

    def set_color(self, color: str) -> None:
        self.color = color
        name = COLOR_NAMES.get(color.lower(), color.upper())
        self.setToolTip(name)
        self.setAccessibleName(f"Color: {name}")
        self.update()

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
    next_step = Signal(int)         # the Step tool's next number (the Numbering section)
    select_steps = Signal()         # Select all steps: select every step number to restyle them together
    image_action = Signal(str)      # rotate_left | rotate_right | flip_horizontal | flip_vertical | resize
    pick_color = Signal()           # the eyedropper button: start (or stop) picking a color from the snip
    style_saved = Signal(str)       # the selection's look was saved as a quick style for this tool
    edges_changed = Signal(object)  # Edges: the snip's border and torn sides
    source_stamp = Signal()         # add the "App name and date" text stamp
    cutout_changed = Signal(dict)   # the Cut out tool's direction or seam
    editing_done = Signal()         # finished typing in a field: give the keyboard back to the canvas
    arrange = Signal(str)           # align left|center|right|top|middle|bottom, distribute_horizontal|_vertical
    tool_default = Signal(str)      # "use": the selection's look becomes the tool's; "reset": the usual look

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
        self._next_step = 1
        self._selected_count = 0
        self._style_color = ""
        self._sections: dict[str, QWidget] = {}
        self._switches: dict[str, ToggleSwitch] = {}

        body = QWidget()
        body.setObjectName("PanelBody")
        self._column = QVBoxLayout(body)
        self._column.setContentsMargins(0, 0, 0, 16)
        self._column.setSpacing(0)
        self._build_header()
        self._build_default()
        self._build_quick()
        self._build_shape()
        self._build_arrow()
        self._build_numbering()
        self._build_stamps()
        self._build_colors()
        self._build_size()
        self._build_look()
        self._build_text()
        self._build_effects()
        self._build_fill()  # right under the Fill switch, so turning it on doesn't move the switch
        self._build_dim()
        self._build_crop()
        self._build_cutout()
        self._build_arrange()
        self._build_image()
        self._build_edges()
        self._build_layers()
        self._column.addStretch(1)
        self.setWidget(body)
        # Controls are reachable with Tab but don't take the keyboard when clicked; you can type a font size.
        for w in body.findChildren(QWidget):
            w.setFocusPolicy(Qt.TabFocus if isinstance(w, (QAbstractButton, QSlider)) else Qt.NoFocus)
        self._font.setFocusPolicy(Qt.StrongFocus)
        self._font.lineEdit().setFocusPolicy(Qt.StrongFocus)
        self._font.editingFinished.connect(self.editing_done)
        self._steps.next.setFocusPolicy(Qt.StrongFocus)
        self._steps.next.lineEdit().setFocusPolicy(Qt.StrongFocus)
        self._steps.next.editingFinished.connect(self.editing_done)
        self._format.font.setFocusPolicy(Qt.StrongFocus)
        self._format.font.lineEdit().setFocusPolicy(Qt.StrongFocus)
        self._hex.setFocusPolicy(Qt.StrongFocus)
        self._hex.editingFinished.connect(self.editing_done)
        self._rotation.setFocusPolicy(Qt.StrongFocus)
        self._rotation.lineEdit().setFocusPolicy(Qt.StrongFocus)
        self._rotation.editingFinished.connect(self.editing_done)
        colors.notifier().changed.connect(self._refresh_colors)
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

    def _build_default(self) -> None:
        col = self._section("default", None)
        row = QHBoxLayout()
        row.setSpacing(6)
        self._default_buttons: dict[str, QPushButton] = {}
        for key, label, tip in (("use", "Use as default", "This look becomes what the tool draws next"),
                                ("reset", "Reset tool", "The tool's usual look (what you've drawn keeps its own)")):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=key: self.tool_default.emit(k))
            self._default_buttons[key] = b
            row.addWidget(b, 1)
        col.addLayout(row)

    def _build_quick(self) -> None:
        col = self._section("quick", "Quick styles")
        self._quick = QuickStyles()
        self._quick.apply.connect(lambda changes: self._emit(**changes))
        self._quick.saved.connect(self.style_saved)
        self._tiles = self._quick.tiles
        col.addWidget(self._quick)

    def _build_shape(self) -> None:
        col = self._section("shape", "Shape")
        self._shapes = ShapeControls(NoWheelSlider)
        self._shapes.changed.connect(lambda changes: self._emit(**changes))
        col.addWidget(self._shapes)

    def _build_arrow(self) -> None:
        col = self._section("arrow", "Heads")
        self._arrow = ArrowControls()
        self._arrow.changed.connect(lambda changes: self._emit(**changes))
        col.addWidget(self._arrow)

    def _build_numbering(self) -> None:
        col = self._section("numbering", "Numbering")
        self._steps = StepControls()
        self._steps.changed.connect(lambda changes: self._emit(**changes))
        self._steps.next_number.connect(self.next_step)
        self._steps.select_all_requested.connect(self.select_steps)
        col.addWidget(self._steps)

    def _build_dim(self) -> None:
        col = self._section("dim", None)
        self._dim = DimControls(NoWheelSlider)
        self._dim.changed.connect(lambda changes: self._emit(**changes))
        col.addWidget(self._dim)

    def _build_stamps(self) -> None:
        col = self._section("stamp", "Stamp")
        grid = QGridLayout()
        grid.setSpacing(4)
        self._stamp_group = QButtonGroup(self)
        self._stamp_buttons: dict[str, QToolButton] = {}
        for i, (name, (label, _svg)) in enumerate(STAMPS.items()):
            grid.addWidget(self._stamp_choice(name, label), i // STAMP_COLUMNS, i % STAMP_COLUMNS)
        col.addLayout(grid)
        self._mine_label = QLabel("YOUR OWN")
        self._mine_label.setProperty("sfRole", "section")
        col.addWidget(self._mine_label)
        self._mine_grid = QGridLayout()
        self._mine_grid.setSpacing(4)
        self._custom_buttons: dict[str, QToolButton] = {}
        col.addLayout(self._mine_grid)
        row = QVBoxLayout()  # one above the other: side by side they're wider than the panel
        row.setSpacing(6)
        self._add_stamp = QPushButton("Add your own…")
        self._add_stamp.setToolTip("Add a picture (PNG, JPG, WebP, SVG...) as a stamp of your own")
        self._add_stamp.setAccessibleName("Add your own stamp")
        self._add_stamp.clicked.connect(self._pick_stamp_files)
        row.addWidget(self._add_stamp)
        self._source_stamp = QPushButton("App name and date")
        self._source_stamp.setAccessibleName("Add an app name and date stamp")
        self._source_stamp.clicked.connect(self.source_stamp)
        row.addWidget(self._source_stamp)
        col.addLayout(row)
        self.set_source_stamp_available(True)
        self._fill_custom_stamps()
        custom_stamps.notifier().changed.connect(self._fill_custom_stamps)

    def _stamp_choice(self, name: str, label: str) -> QToolButton:
        b = QToolButton()
        b.setObjectName("StampChoice")
        b.setIcon(stamp_icon(name, 64))
        b.setIconSize(QSize(28, 28))
        b.setCheckable(True)
        b.setToolTip(label)
        b.setAccessibleName(label)
        b.clicked.connect(lambda _=False, n=name: self._emit(stamp=n))
        self._stamp_group.addButton(b)
        if custom_stamps.is_custom(name):
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda pos, n=name, w=b: self._custom_stamp_menu(n, w, pos))
            b.setToolTip(f"{label} (right-click to remove)")
            self._custom_buttons[name] = b
        else:
            self._stamp_buttons[name] = b
        return b

    def _fill_custom_stamps(self) -> None:
        """Your own stamps' buttons, after the built-in ones, following the Stamps folder."""
        chosen = next((n for n, b in self._custom_buttons.items() if b.isChecked()), None)
        for b in self._custom_buttons.values():
            self._stamp_group.removeButton(b)
            b.hide()
            b.setParent(None)
            b.deleteLater()
        self._custom_buttons.clear()
        names = custom_stamps.names()
        for i, name in enumerate(names):
            b = self._stamp_choice(name, custom_stamps.label_of(name))
            b.setFocusPolicy(Qt.TabFocus)
            self._mine_grid.addWidget(b, i // STAMP_COLUMNS, i % STAMP_COLUMNS)
            if name == chosen:
                b.setChecked(True)
        self._mine_label.setVisible(bool(names))

    def _custom_stamp_menu(self, name: str, button: QToolButton, pos) -> None:
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        remove = menu.addAction(f"Remove {custom_stamps.label_of(name)}")
        remove.triggered.connect(lambda: self._confirm_remove_stamp(name))
        menu.exec(button.mapToGlobal(pos))

    def _confirm_remove_stamp(self, name: str) -> None:
        from PySide6.QtWidgets import QMessageBox

        label = custom_stamps.label_of(name)
        answer = QMessageBox.question(
            self, "Remove stamp", f"Remove the stamp \u201c{label}\u201d? Snips that use it show an empty frame "
            "in its place.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.remove_custom_stamp(name)

    def remove_custom_stamp(self, name: str) -> None:
        custom_stamps.remove(name)

    def _pick_stamp_files(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        paths, _chosen = QFileDialog.getOpenFileNames(
            self, "Add your own stamps", str(Path.home() / "Pictures"),
            "Pictures (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.svg)")
        added = [n for n in (custom_stamps.add(Path(p)) for p in paths) if n]
        if len(added) < len(paths):
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(self, "Add your own stamps",
                                f"Couldn't add {len(paths) - len(added)} of the pictures: SnipForge couldn't read "
                                f"them, an SVG links to other files, or you have {custom_stamps.MAX_STAMPS} stamps "
                                "already.")
        if added:
            self._emit(stamp=added[-1])  # the newest is picked for the next drop
            for name, b in self._custom_buttons.items():
                b.setChecked(name == added[-1])

    def add_stamp_button(self) -> QPushButton:
        return self._add_stamp

    def custom_stamp_buttons(self) -> dict[str, QToolButton]:
        return dict(self._custom_buttons)

    def _build_colors(self) -> None:
        col = self._section("color", None)
        head = QHBoxLayout()
        head.setSpacing(4)
        label = QLabel("COLOR")
        label.setProperty("sfRole", "section")
        head.addWidget(label)
        head.addStretch(1)
        self._hex = QLineEdit()
        self._hex.setObjectName("HexColor")
        self._hex.setPlaceholderText("#rrggbb")
        self._hex.setMaxLength(7)
        self._hex.setFixedWidth(78)
        self._hex.setToolTip("The color as hex: type one, like #1a73e8")
        screen_reader_name(self._hex, "Color as hex")
        self._hex.editingFinished.connect(self._hex_typed)
        head.addWidget(self._hex)
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
        self._swatches: list[Swatch] = []
        for n, color in enumerate(colors.palette()):  # checked by hand: a custom color clears every ring
            swatch = Swatch(color)
            swatch.clicked.connect(lambda _=False, i=n: self._pick_swatch(self._swatches[i].color))
            swatch.setContextMenuPolicy(Qt.CustomContextMenu)
            swatch.customContextMenuRequested.connect(lambda pos, i=n: self._swatch_menu(i, pos))
            swatch.setToolTip(swatch.toolTip() + " (right-click to change it)")
            self._swatches.append(swatch)
            row.addWidget(swatch)
        row.addStretch(1)
        col.addLayout(row)
        self._recent_label = QLabel("RECENT")
        self._recent_label.setProperty("sfRole", "section")
        col.addWidget(self._recent_label)
        self._recent_row = QHBoxLayout()
        self._recent_row.setSpacing(2)
        self._recent_row.addStretch(1)
        self._recent: list[Swatch] = []
        col.addLayout(self._recent_row)
        self._refresh_colors()

    def _refresh_colors(self) -> None:
        """The swatches follow your palette and the colors you've used lately."""
        for swatch, color in zip(self._swatches, colors.palette(), strict=True):
            if swatch.color != color:
                swatch.set_color(color)
                swatch.setToolTip(swatch.toolTip() + " (right-click to change it)")
        for old in self._recent:
            self._recent_row.removeWidget(old)
            old.hide()
            old.deleteLater()
        self._recent = []
        for color in colors.recent():
            swatch = Swatch(color)
            swatch.setFocusPolicy(Qt.TabFocus)
            swatch.clicked.connect(lambda _=False, c=color: self._pick_swatch(c))
            self._recent_row.insertWidget(len(self._recent), swatch)
            self._recent.append(swatch)
        self._recent_label.setVisible(bool(self._recent))
        self._check_swatches(self._style_color)

    def _check_swatches(self, color: str) -> None:
        """Ring the swatch that is `color` (one at most: the palette's first, else a recent one)."""
        found = False
        for swatch in (*self._swatches, *self._recent):
            on = not found and swatch.color.lower() == (color or "").lower()
            swatch.setChecked(on)
            found = found or on

    def _swatch_menu(self, index: int, pos) -> None:
        swatch = self._swatches[index]
        menu = QMenu(self)
        change = menu.addAction("Change this color…")
        change.triggered.connect(lambda: self._change_swatch(index))
        reset = menu.addAction("Reset the palette")
        reset.setEnabled(colors.palette() != list(colors.DEFAULT_PALETTE))
        reset.triggered.connect(colors.reset_palette)
        menu.exec(swatch.mapToGlobal(pos))

    def _change_swatch(self, index: int) -> None:
        color = QColorDialog.getColor(QColor(self._swatches[index].color), self, "Palette color")
        if color.isValid():
            colors.set_palette_color(index, color.name())

    def _hex_typed(self) -> None:
        color = colors.normalize(self._hex.text())
        if color is None:
            self._hex.setText(self._style_color)  # not a color: back to what it was
            return
        self._hex.setText(color)
        if color != (self._style_color or "").lower():
            self._style_color = color
            self._check_swatches(color)
            colors.remember(color)
            self._emit(color=color)

    def _build_fill(self) -> None:
        col = self._section("fill", None)
        self._fill = FillControls(Swatch, NoWheelSlider)
        self._fill.changed.connect(lambda changes: self._emit(**changes))
        col.addWidget(self._fill)

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

    def _build_look(self) -> None:
        col = self._section("look", None)
        head = QHBoxLayout()
        label = QLabel("OPACITY")
        label.setProperty("sfRole", "section")
        self._opacity_value = QLabel()
        self._opacity_value.setProperty("sfRole", "secondary")
        head.addWidget(label)
        head.addStretch(1)
        head.addWidget(self._opacity_value)
        col.addLayout(head)
        self._opacity = NoWheelSlider(Qt.Horizontal)
        self._opacity.setRange(5, 100)
        self._opacity.setAccessibleName("Opacity")
        self._opacity.valueChanged.connect(self._opacity_moved)
        col.addWidget(self._opacity)
        row = QHBoxLayout()
        row.addWidget(QLabel("Rotation"))
        row.addStretch(1)
        self._rotation = FocusedSpinBox()
        self._rotation.setRange(-359, 359)
        self._rotation.setSuffix("°")
        self._rotation.setWrapping(True)
        self._rotation.setToolTip("Turn it about its middle, in degrees")
        screen_reader_name(self._rotation, "Rotation in degrees")
        self._rotation.valueChanged.connect(lambda v: self._emit(rotation=int(v) % 360))
        row.addWidget(self._rotation)
        col.addLayout(row)

    def _opacity_moved(self, value: int) -> None:
        self._opacity_value.setText(f"{value}%")
        self._emit(opacity=int(value))

    def _build_arrange(self) -> None:
        col = self._section("arrange", "Arrange")
        self._arrange_buttons: dict[str, QToolButton] = {}
        for keys_ in (("left", "center", "right", "top", "middle", "bottom"), ("h", "v")):
            row = QHBoxLayout()
            row.setSpacing(4)
            for key in keys_:
                b = QToolButton()
                b.setObjectName("ImageAction")
                b.setIconSize(QSize(18, 18))
                b.setCursor(Qt.PointingHandCursor)
                tip = ARRANGE_TIPS[key]
                b.setToolTip(tip)
                b.setAccessibleName(tip)
                b.clicked.connect(lambda _=False, k=key: self.arrange.emit(ARRANGE_ACTIONS[k]))
                self._arrange_buttons[key] = b
                row.addWidget(b)
            row.addStretch(1)
            col.addLayout(row)

    def _build_layers(self) -> None:
        col = self._section("layers", "Layers")
        self._layers = LayersList()
        col.addWidget(self._layers)

    def _build_text(self) -> None:
        col = self._section("text", "Text")
        row = QHBoxLayout()
        label = QLabel("Font size")
        row.addWidget(label)
        row.addStretch(1)
        self._font = FocusedSpinBox()
        self._font.setRange(8, 96)
        screen_reader_name(self._font, "Font size")
        self._font.valueChanged.connect(lambda v: self._emit(font_size=int(v)))
        row.addWidget(self._font)
        col.addLayout(row)
        self._format = TextFormatControls()
        self._format.changed.connect(lambda changes: self._emit(**changes))
        col.addWidget(self._format)
        self._text_color = TextColorControls(Swatch)  # callouts: a text box's Color is its text's
        self._text_color.changed.connect(lambda changes: self._emit(**changes))
        col.addWidget(self._text_color)

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
        self._line_row = QWidget()
        row = QHBoxLayout(self._line_row)
        row.setContentsMargins(0, 2, 0, 2)
        row.addWidget(QLabel("Line"), 1)
        self._line = Segmented(list(LINE_STYLES), value="solid")
        for key, label in LINE_STYLES:  # icons, as for the callout's shapes; the name stays in the tooltip
            b = self._line.button(key)
            b.setToolTip(f"{label} line")
            b.setAccessibleName(f"{label} line")
            b.setText("")
            b.setToolButtonStyle(Qt.ToolButtonIconOnly)
            b.setIconSize(QSize(20, 20))
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._line_loaded = "solid"
        self._line.changed.connect(self._line_chosen)
        row.addWidget(self._line)
        col.addWidget(self._line_row)
        for key in SWITCHES:
            switch = self._switch_row(col, key, SWITCH_LABELS[key])
            switch.toggled.connect(lambda on, k=key: self._emit(**{k: on}))

    def _line_chosen(self, key: str) -> None:
        if key != self._line_loaded:  # clicking the line you have already adds nothing to undo
            self._line_loaded = key
            self._emit(**LINE_CHANGES[key])

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
    def load(self, kind: str, style: Style | None, has_selection: bool, next_step: int = 1,
             selected_count: int | None = None, next_numbering: str | None = None) -> None:
        self._loading = True
        self._kind = kind
        self._next_step = next_step
        self._next_numbering = next_numbering
        self._selected_count = (1 if has_selection else 0) if selected_count is None else selected_count
        fields = STYLE_FIELDS.get(kind, ()) if style else ()
        self._title.setText(TITLES.get(kind, kind.title()))
        self._chip.setVisible(has_selection)
        self._hint.setText("" if has_selection else HINTS.get(kind, "").format(alt=alt_key()))
        self._hint.setVisible(bool(self._hint.text()))
        show = {
            "quick": bool(fields) and self._quick.worth_showing(kind, has_selection), "shape": "shape" in fields,
            "arrow": "heads" in fields, "numbering": "numbering" in fields,
            "stamp": "stamp" in fields,
            "color": "color" in fields, "size": "width" in fields, "text": "font_size" in fields,
            "fill": "fill_color" in fields and bool(style and style.fill),
            "effects": any(f in fields for f in (*SWITCHES, "dash")), "crop": kind == "crop",
            "image": kind == "select", "dim": "dim" in fields, "look": "opacity" in fields,
            "cutout": kind == "cutout", "edges": kind == "select",
            "arrange": has_selection and kind not in ("crop", "cutout"),
            "default": bool(fields) and kind in self._tool_kinds(),
            "layers": kind == "select" and self._layers.has_rows(),
        }
        for name, box in self._sections.items():
            box.setVisible(show[name])
        for key in SWITCHES:
            self._switches[key].parentWidget().setVisible(key in fields)
        self._line_row.setVisible("dash" in fields)
        self._fill.set_noun("background" if kind == "text" else "fill")
        self._switches["fill"].property("rowLabel").setText(
            {"text": "Background", "callout": "Filled"}.get(kind, "Fill"))
        self._size_label.setText(self.size_label().upper())
        self._slider.setAccessibleName(self.size_label())
        self._default_buttons["use"].setVisible(has_selection)
        count = self._selected_count
        for key, b in self._arrange_buttons.items():
            b.setEnabled(count >= (3 if key in ("h", "v") else 2))
        if style:
            self._load_style(style)
        self._quick.load(kind, style, has_selection)
        self._loading = False

    @staticmethod
    def _tool_kinds() -> set[str]:
        from .style import DEFAULT_TOOL_STYLES
        return set(DEFAULT_TOOL_STYLES)

    def _load_style(self, style: Style) -> None:
        self._style_color = style.color
        self._check_swatches(style.color)
        self._hex.setText(style.color.lower())
        self._opacity.setValue(int(style.opacity))
        self._opacity_value.setText(f"{int(style.opacity)}%")
        self._rotation.setValue(int(style.rotation))
        self._slider.setValue(int(round(style.width)))
        self._size_value.setText(f"{int(round(style.width))}")
        self._font.setValue(int(style.font_size))
        for key in SWITCHES:
            self._switches[key].setChecked(bool(getattr(style, key)))
        self._line_loaded = line_style(style)
        self._line.set_value(self._line_loaded)
        self._fill.load(self._kind, style)
        self._shapes.load(self._kind, style)
        self._arrow.load(style)
        self._steps.load(style, self._next_step, self._next_numbering)
        self._format.load(self._kind, style)
        self._text_color.setVisible("text_color" in STYLE_FIELDS.get(self._kind, ()))
        self._text_color.load(self._kind, style)
        self._dim.load(style)
        for name, button in (*self._stamp_buttons.items(), *self._custom_buttons.items()):
            button.setChecked(name == style.stamp)

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
        for controls in (self._shapes, self._arrow, self._steps, self._format):
            controls.retheme()
        for key, _label in LINE_STYLES:
            self._line.button(key).setIcon(icon(f"line_{key}"))
        self._custom.setIcon(icon("plus"))
        self._fill.custom.setIcon(icon("plus"))
        self._text_color.retheme(icon("plus"))
        self._cutout.retheme()
        self._picker.setIcon(icon("pipette"))
        for action, icon_name, _tip in IMAGE_ACTIONS:
            self._image_buttons[action].setIcon(icon(icon_name))
        for key, b in self._arrange_buttons.items():
            b.setIcon(icon(ARRANGE_ICONS[key]))
        self._layers.retheme()
        self.viewport().update()
        for w in (*self._swatches, *self._recent, *self._fill.swatches.values(), self._fill.same, *self._tiles,
                  *self._quick.saved_tiles, *self._switches.values()):
            w.update()

    def _pick_swatch(self, color: str) -> None:
        self._style_color = color
        self._check_swatches(color)
        self._hex.setText(color.lower())
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
            colors.remember(color.name())
            self._pick_swatch(color.name())

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

    def line_control(self) -> Segmented:
        return self._line

    def fill_controls(self) -> FillControls:
        return self._fill

    def swatch(self, color: str) -> Swatch:
        return next(s for s in self._swatches if s.color.lower() == color.lower())

    def swatch_at(self, index: int) -> Swatch:
        return self._swatches[index]

    def recent_swatches(self) -> list[Swatch]:
        return list(self._recent)

    def hex_field(self) -> QLineEdit:
        return self._hex

    def opacity_slider(self) -> QSlider:
        return self._opacity

    def rotation_box(self) -> FocusedSpinBox:
        return self._rotation

    def arrange_button(self, key: str) -> QToolButton:
        return self._arrange_buttons[key]

    def default_button(self, key: str) -> QPushButton:
        return self._default_buttons[key]

    def text_color_controls(self) -> TextColorControls:
        return self._text_color

    def step_controls(self) -> StepControls:
        return self._steps

    def layers_list(self) -> LayersList:
        return self._layers

    def quick_tile(self, index: int) -> QuickStyleTile:
        return self._tiles[index]

    def saved_tile(self, index: int) -> SavedStyleTile:
        return self._quick.saved_tiles[index]

    def saved_count(self) -> int:
        return len(self._quick.saved_tiles)

    def save_button(self) -> QPushButton:
        return self._quick.save_button

    def remove_all_button(self) -> QPushButton:
        return self._quick.remove_all_button

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
