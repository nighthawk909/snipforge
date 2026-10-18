"""The panel's controls for the snip's edges (border and torn sides) and for the Cut out tool."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..icons import icon
from ..widgets import Segmented, ToggleSwitch
from .edges import MAX_BORDER, SIDES, Edges
from .style import PALETTE

SIDE_LABELS = {"top": "Top", "right": "Right", "bottom": "Bottom", "left": "Left"}


class EdgeControls(QWidget):
    """Border on or off, its width and color, and which sides are torn."""

    changed = Signal(object)  # Edges

    def __init__(self, swatch_factory, parent=None) -> None:
        super().__init__(parent)
        self._edges = Edges()
        self._loading = False
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)

        row = QHBoxLayout()
        row.addWidget(QLabel("Border"), 1)
        self.border = ToggleSwitch("Border")
        self.border.toggled.connect(self._border_toggled)
        row.addWidget(self.border)
        column.addLayout(row)

        self._details = QWidget()
        details = QVBoxLayout(self._details)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(6)
        self.width_slider = QSlider(Qt.Horizontal)
        self.width_slider.setRange(1, MAX_BORDER)
        self.width_slider.setAccessibleName("Border width")
        self.width_slider.valueChanged.connect(lambda v: self._emit(border=int(v)))
        details.addWidget(self.width_slider)
        swatches = QHBoxLayout()
        swatches.setSpacing(2)
        self.swatches = {}
        for color in PALETTE:
            swatch = swatch_factory(color)
            swatch.clicked.connect(lambda _=False, c=color: self._emit(color=c))
            self.swatches[color] = swatch
            swatches.addWidget(swatch)
        swatches.addStretch(1)
        details.addLayout(swatches)
        column.addWidget(self._details)

        torn_label = QLabel("Torn edges")
        column.addWidget(torn_label)
        sides = QGridLayout()  # two by two, so the panel stays narrow
        sides.setSpacing(4)
        self.sides: dict[str, QToolButton] = {}
        for side in SIDES:
            chip = QToolButton()
            chip.setObjectName("Chip")
            chip.setText(SIDE_LABELS[side])
            chip.setCheckable(True)
            chip.setAccessibleName(f"Torn {SIDE_LABELS[side].lower()} edge")
            chip.setToolTip(f"Tear the {SIDE_LABELS[side].lower()} edge")
            chip.toggled.connect(lambda on, s=side: self._side_toggled(s, on))
            self.sides[side] = chip
            sides.addWidget(chip, *divmod(len(self.sides) - 1, 2))
        sides.setColumnStretch(2, 1)  # chips stay together on the left
        column.addLayout(sides)
        self.load(Edges())

    def load(self, edges: Edges) -> None:
        self._loading = True
        self._edges = edges
        self.border.setChecked(bool(edges.border))
        self._details.setVisible(bool(edges.border))
        self.width_slider.setValue(edges.border or 4)
        for color, swatch in self.swatches.items():
            swatch.setChecked(color.lower() == edges.color.lower())
        for side, chip in self.sides.items():
            chip.setChecked(side in edges.torn)
        self._loading = False

    def _border_toggled(self, on: bool) -> None:
        self._details.setVisible(on)
        self._emit(border=self.width_slider.value() if on else 0)

    def _side_toggled(self, side: str, on: bool) -> None:
        if not self._loading:
            self._edges = self._edges.with_side(side, on)
            self.changed.emit(self._edges)

    def _emit(self, **changes) -> None:
        if self._loading:
            return
        self._edges = replace(self._edges, **changes)
        for color, swatch in self.swatches.items():
            swatch.setChecked(color.lower() == self._edges.color.lower())
        self.changed.emit(self._edges)


class CutoutControls(QWidget):
    """Which way the Cut out tool removes a strip, and how the rest is joined."""

    changed = Signal(dict)  # {"direction": ..., "seam": ...}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        column.addWidget(QLabel("Strip"))
        self.direction = Segmented([("vertical", "Vertical"), ("horizontal", "Horizontal")])
        for key, tip in (("vertical", "Vertical strip: take out columns and join left and right"),
                         ("horizontal", "Horizontal strip: take out rows and join top and bottom")):
            button = self.direction.button(key)
            button.setToolTip(tip)
            button.setAccessibleName(tip.split(":")[0])
            button.setText("")  # icon only (see retheme); the name stays in the tooltip and for screen readers
            button.setToolButtonStyle(Qt.ToolButtonIconOnly)
            button.setIconSize(QSize(20, 20))
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.direction.changed.connect(lambda v: self.changed.emit({"direction": v}))
        column.addWidget(self.direction)
        column.addWidget(QLabel("Seam"))
        self.seam = Segmented([("straight", "Straight"), ("torn", "Torn")])
        self.seam.changed.connect(lambda v: self.changed.emit({"seam": v}))
        column.addWidget(self.seam)

    def retheme(self) -> None:
        self.direction.button("vertical").setIcon(icon("strip_vertical"))
        self.direction.button("horizontal").setIcon(icon("strip_horizontal"))

    def load(self, options: dict) -> None:
        self.direction.set_value(options.get("direction", "vertical"))
        self.seam.set_value(options.get("seam", "straight"))
