"""The panel's Fill controls: the fill's color (or the outline's) and how see-through it is."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QColorDialog, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget

from .. import theme
from .style import PALETTE, Style, fill_opacity


class SameAsOutline(QPushButton):
    """The first fill swatch: no color of its own, the outline's. Draws a tint ringed in that color."""

    def __init__(self) -> None:
        super().__init__()
        self.outline = "#ff3b30"
        self.setFixedSize(24, 24)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Same color as the outline")
        self.setAccessibleName("Fill color: same as the outline")

    def paintEvent(self, _e) -> None:  # noqa: N802 - Qt API
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.isChecked():
            p.setPen(QPen(QColor(theme.current().accent.fill), 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(1, 1, 22, 22))
        dot = QRectF(4.5, 4.5, 15, 15)
        tint = QColor(self.outline)
        tint.setAlpha(90)
        p.setPen(Qt.NoPen)
        p.setBrush(tint)
        p.drawEllipse(dot)
        p.setBrush(QColor(self.outline))
        p.drawPie(dot, 90 * 16, 180 * 16)  # half the outline's color, half its tint: "from the outline"
        p.setPen(QPen(QColor(self.outline), 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(dot)
        p.end()


class FillControls(QWidget):
    """Emits `changed` with {"fill_color": ...} or {"fill_opacity": ...}. The panel passes its swatch and
    slider classes so these match the Color section's."""

    changed = Signal(dict)

    def __init__(self, swatch_factory, slider_factory, parent=None) -> None:
        super().__init__(parent)
        self._loading = False
        self._style = Style()
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(4)
        self.title = QLabel("FILL")
        self.title.setProperty("sfRole", "section")
        head.addWidget(self.title)
        head.addStretch(1)
        self.custom = QToolButton()
        self.custom.setObjectName("CustomColor")
        self.custom.setToolTip("Custom fill color")
        self.custom.setAccessibleName("Custom fill color")
        self.custom.clicked.connect(self._pick_custom)
        head.addWidget(self.custom)
        column.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(2)
        self.same = SameAsOutline()
        self.same.clicked.connect(lambda: self._pick(""))
        row.addWidget(self.same)
        self.swatches = {}
        for color in PALETTE:
            swatch = swatch_factory(color)
            swatch.setAccessibleName(f"Fill {swatch.accessibleName().lower()}")
            swatch.clicked.connect(lambda _=False, c=color: self._pick(c))
            self.swatches[color] = swatch
            row.addWidget(swatch)
        row.addStretch(1)
        column.addLayout(row)

        opacity_head = QHBoxLayout()
        opacity_head.addWidget(QLabel("Opacity"))
        opacity_head.addStretch(1)
        self.opacity_value = QLabel()
        self.opacity_value.setProperty("sfRole", "secondary")
        opacity_head.addWidget(self.opacity_value)
        column.addLayout(opacity_head)
        self.opacity = slider_factory(Qt.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setSingleStep(5)
        self.opacity.setPageStep(10)
        self.opacity.setAccessibleName("Fill opacity")
        self.opacity.valueChanged.connect(self._opacity_moved)
        column.addWidget(self.opacity)

    def load(self, kind: str, style: Style) -> None:
        self._loading = True
        try:
            self._style = style
            self.same.outline = style.color
            self.same.setChecked(not style.fill_color)
            self.same.update()
            for color, swatch in self.swatches.items():
                swatch.setChecked(color.lower() == style.fill_color.lower())
            shown = fill_opacity(kind, style)
            self.opacity.setValue(shown)
            self.opacity_value.setText(f"{shown}%")
        finally:
            self._loading = False

    def _pick(self, color: str) -> None:
        self.same.setChecked(not color)
        for c, swatch in self.swatches.items():
            swatch.setChecked(c == color)
        self._emit(fill_color=color)

    def _pick_custom(self) -> None:
        start = QColor(self._style.fill_color or self._style.color)
        color = QColorDialog.getColor(start, self, "Fill color")
        if color.isValid():
            self._pick(color.name())

    def _opacity_moved(self, value: int) -> None:
        self.opacity_value.setText(f"{value}%")
        self._emit(fill_opacity=int(value))

    def _emit(self, **changes) -> None:
        if not self._loading:
            self.changed.emit(changes)

