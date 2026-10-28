"""The mini toolbar: a small row of buttons just above whatever you've selected on the snip, for the
things you do most without reaching for the panel: its color, duplicate, bring forward, send
backward, lock, delete.

It sits on the canvas's viewport, follows the selection as it's moved, zoomed or scrolled, keeps out
of the way while you drag or type, and never leaves the viewport (it slides in from the edges).
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QMenu, QToolButton, QWidget, QWidgetAction

from .. import theme
from ..icons import icon
from ..wording import keys

GAP = 8  # between the toolbar and the selection
MARGIN = 6  # from the viewport's edges
BUTTONS = (  # name, icon, tooltip
    ("color", "", "Color"),
    ("duplicate", "copy", "Duplicate ({ctrl_d})"),
    ("forward", "chevron_up", "Bring forward ({ctrl_close})"),
    ("backward", "chevron_down", "Send backward ({ctrl_open})"),
    ("lock", "lock", "Lock it where it is"),
    ("delete", "trash", "Delete (Del)"),
)


class ColorDot(QToolButton):
    """The selection's color; click for the palette."""

    def __init__(self) -> None:
        super().__init__()
        self.color = "#ff3b30"
        self.setToolTip("Color")
        self.setAccessibleName("Color")

    def paintEvent(self, e) -> None:  # noqa: N802 - Qt API
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(theme.current().palette.text3), 1))
        p.setBrush(QColor(self.color))
        r = self.rect().adjusted(7, 7, -7, -7)
        p.drawEllipse(QRectF(r))
        p.end()


class _Palette(QWidget):
    """The palette that drops from the color dot."""

    picked = Signal(str)

    def __init__(self, swatches: list[str]) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 6, 6, 6)
        row.setSpacing(4)
        for color in swatches:
            b = QToolButton()
            b.setFixedSize(22, 22)
            b.setToolTip(color.upper())
            b.setAccessibleName(f"Color {color.upper()}")
            b.setStyleSheet(f"background: {color}; border: 1px solid rgba(128,128,128,0.6); border-radius: 11px;")
            b.clicked.connect(lambda _=False, c=color: self.picked.emit(c))
            row.addWidget(b)


class MiniToolbar(QWidget):
    color_picked = Signal(str)
    duplicate = Signal()
    forward = Signal()
    backward = Signal()
    lock = Signal(bool)  # the new locked state
    delete = Signal()

    def __init__(self, viewport: QWidget) -> None:
        super().__init__(viewport)
        self.setObjectName("MiniToolbar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self._swatches: list[str] = []
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(2)
        self._buttons: dict[str, QToolButton] = {}
        names = {"ctrl_d": keys("Ctrl+D"), "ctrl_close": keys("Ctrl+]"), "ctrl_open": keys("Ctrl+[")}
        for name, _icon_name, tip in BUTTONS:
            b = ColorDot() if name == "color" else QToolButton()
            b.setObjectName("MiniButton")
            b.setAutoRaise(True)
            b.setIconSize(QSize(16, 16))
            b.setFixedSize(28, 28)
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.setToolTip(tip.format(**names))
            b.setAccessibleName(tip.format(**names).split(" (")[0])
            self._buttons[name] = b
            row.addWidget(b)
        self._buttons["lock"].setCheckable(True)
        self._buttons["color"].clicked.connect(self._show_palette)
        self._buttons["duplicate"].clicked.connect(self.duplicate)
        self._buttons["forward"].clicked.connect(self.forward)
        self._buttons["backward"].clicked.connect(self.backward)
        self._buttons["lock"].toggled.connect(self.lock)
        self._buttons["delete"].clicked.connect(self.delete)
        self.hide()
        self.retheme()

    # ------------------------------------------------------------------ showing
    def show_for(self, bounds: QRect | None, color: str | None, locked: bool) -> None:
        """Place it above `bounds` (viewport coordinates), or hide it when there's nothing (None)."""
        if bounds is None:
            self.hide()
            return
        dot = self._buttons["color"]
        dot.setVisible(color is not None)
        if color:
            dot.color = color
            dot.update()
        lock = self._buttons["lock"]
        lock.blockSignals(True)
        lock.setChecked(locked)
        lock.setToolTip("Locked: click to unlock" if locked else "Lock it where it is")
        lock.blockSignals(False)
        for name in ("duplicate", "delete"):
            self._buttons[name].setEnabled(not locked)
        self.adjustSize()
        size = self.sizeHint()
        parent = self.parentWidget().rect()
        x = bounds.center().x() - size.width() // 2
        y = bounds.top() - GAP - size.height()
        if y < MARGIN:  # no room above: below it instead
            y = min(bounds.bottom() + GAP, parent.bottom() - MARGIN - size.height())
        x = max(MARGIN, min(parent.right() - MARGIN - size.width(), x))
        y = max(MARGIN, y)
        self.setGeometry(QRect(QPoint(x, y), size))
        self.show()
        self.raise_()

    def set_swatches(self, swatches: list[str]) -> None:
        self._swatches = list(swatches)

    def _show_palette(self) -> None:
        menu = QMenu(self)
        palette = _Palette(self._swatches)
        palette.picked.connect(lambda c: (menu.close(), self.color_picked.emit(c)))
        action = QWidgetAction(menu)
        action.setDefaultWidget(palette)
        menu.addAction(action)
        dot = self._buttons["color"]
        menu.exec(dot.mapToGlobal(dot.rect().bottomLeft()))

    def retheme(self) -> None:
        for name, icon_name, _tip in BUTTONS:
            if icon_name:
                self._buttons[name].setIcon(icon(icon_name))
        self._buttons["color"].update()

    def button(self, name: str) -> QToolButton:
        return self._buttons[name]
