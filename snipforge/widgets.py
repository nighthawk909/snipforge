"""Small shared controls drawn from the theme: a toggle switch and a segmented control."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QButtonGroup, QHBoxLayout, QToolButton, QWidget

from . import theme


class ToggleSwitch(QAbstractButton):
    """On/off switch (Windows 11 style). The label lives next to it; `name` is for screen readers."""

    def __init__(self, name: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(name)
        self.setToolTip(name)

    def sizeHint(self) -> QSize:
        return QSize(40, 22)

    def paintEvent(self, _e) -> None:
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = QRectF(1, (self.height() - 20) / 2, 38, 20)
        on = self.isChecked()
        if not self.isEnabled():
            p.setOpacity(0.45)
        if on:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(t.accent.fill_hover if self.underMouse() else t.accent.fill))
        else:
            p.setPen(QPen(QColor(t.palette.text2 if self.underMouse() else t.palette.text3), 1))
            p.setBrush(QColor(t.palette.surface))
        p.drawRoundedRect(track, 10, 10)
        knob = 12 if on else 10
        x = track.right() - 4 - knob if on else track.left() + 5
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(t.accent.on if on else t.palette.text2))
        p.drawEllipse(QRectF(x, track.center().y() - knob / 2, knob, knob))
        if self.hasFocus() and self.focusPolicy() != Qt.NoFocus:
            p.setOpacity(1.0)
            p.setPen(QPen(QColor(t.accent.fill), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(track.adjusted(-2, -2, 2, 2), 12, 12)
        p.end()

    def enterEvent(self, e) -> None:
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self.update()
        super().leaveEvent(e)


class Segmented(QWidget):
    """A row of mutually exclusive options, e.g. Stable | Preview."""

    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], value: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Segmented")
        self.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(self)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QToolButton] = {}
        for key, label in options:
            b = QToolButton()
            b.setText(label)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolButtonStyle(Qt.ToolButtonTextOnly)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self._group.addButton(b)
            self._buttons[key] = b
            row.addWidget(b)
        self.set_value(value if value is not None else options[0][0])

    @property
    def value(self) -> str:
        return next(k for k, b in self._buttons.items() if b.isChecked())

    def button(self, key: str) -> QToolButton:
        return self._buttons[key]

    def set_value(self, key: str) -> None:
        if key not in self._buttons:
            raise ValueError(f"unknown option {key!r}")
        self._buttons[key].setChecked(True)
