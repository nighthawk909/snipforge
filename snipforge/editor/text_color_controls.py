"""The panel's Text color row for callouts: Automatic (dark or white, whichever reads on the fill),
a palette color, or one of your own."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QColorDialog, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget

from .. import theme
from . import colors
from .items import fill_ink, over_white
from .style import COLOR_NAMES, PALETTE, Style, fill_qcolor


class AutomaticTextColor(QPushButton):
    """The first swatch: no color of its own. Draws "A" in the color the text gets automatically."""

    def __init__(self) -> None:
        super().__init__()
        self.ink, self.paper = QColor("#1c1c1e"), QColor("#ffffff")
        self.setFixedSize(24, 24)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Automatic: dark or white, whichever reads on the fill")
        self.setAccessibleName("Text color: automatic")

    def paintEvent(self, _e) -> None:  # noqa: N802 - Qt API
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.isChecked():
            p.setPen(QPen(QColor(theme.current().accent.fill), 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(1, 1, 22, 22))
        dot = QRectF(4.5, 4.5, 15, 15)
        p.setPen(QPen(QColor(theme.current().palette.text3), 1))
        p.setBrush(self.paper)
        p.drawEllipse(dot)
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSizeF(7.5)
        p.setFont(font)
        p.setPen(self.ink)
        p.drawText(dot, Qt.AlignCenter, "A")
        p.end()


class TextColorControls(QWidget):
    """Emits `changed` with {"text_color": color}, "" for Automatic. The panel passes its swatch class
    so these match the Color section's."""

    changed = Signal(dict)

    def __init__(self, swatch_factory, parent=None) -> None:
        super().__init__(parent)
        self._loading = False
        self._style = Style()
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 4, 0, 0)
        column.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(QLabel("Text color"))
        head.addStretch(1)
        self.custom = QToolButton()
        self.custom.setObjectName("CustomColor")
        self.custom.setToolTip("Custom text color")
        self.custom.setAccessibleName("Custom text color")
        self.custom.clicked.connect(self._pick_custom)
        head.addWidget(self.custom)
        column.addLayout(head)
        row = QHBoxLayout()
        row.setSpacing(2)
        self.automatic = AutomaticTextColor()
        self.automatic.clicked.connect(lambda: self._pick(""))
        row.addWidget(self.automatic)
        self.swatches = {}
        for color in PALETTE:
            swatch = swatch_factory(color)
            swatch.setAccessibleName(f"Text color: {COLOR_NAMES.get(color, color)}")
            swatch.clicked.connect(lambda _=False, c=color: self._pick(c))
            self.swatches[color] = swatch
            row.addWidget(swatch)
        row.addStretch(1)
        column.addLayout(row)

    def load(self, kind: str, style: Style) -> None:
        self._loading = True
        try:
            self._style = style
            self.automatic.ink = fill_ink(kind, style.with_changes(text_color="")) if style.fill else QColor("#1c1c1e")
            # As the callout really shows its fill: a faint one is a pale tint over white.
            self.automatic.paper = over_white(fill_qcolor(kind, style)) if style.fill else QColor("#ffffff")
            self._check(style.text_color)
        finally:
            self._loading = False

    def _check(self, color: str) -> None:
        self.automatic.setChecked(not color)
        self.automatic.update()
        for c, swatch in self.swatches.items():
            swatch.setChecked(c.lower() == color.lower())

    def _pick(self, color: str) -> None:
        self._check(color)
        if not self._loading:
            self.changed.emit({"text_color": color})

    def _pick_custom(self) -> None:
        start = QColor(self._style.text_color or self.automatic.ink.name())
        color = QColorDialog.getColor(start, self, "Text color")
        if color.isValid():
            colors.remember(color.name())  # it shows in the Color section's Recent too
            self._pick(color.name())

    def retheme(self, plus_icon) -> None:
        self.custom.setIcon(plus_icon)
        self.automatic.update()
        for swatch in self.swatches.values():
            swatch.update()
