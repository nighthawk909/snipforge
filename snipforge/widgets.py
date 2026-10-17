"""Small shared controls drawn from the theme: toggle switch, segmented control, sidebar item and
settings row."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .icons import icon
from .theme.tokens import to_qcolor


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


class NavItem(QAbstractButton):
    """A sidebar row: icon (or colored dot), label and a count, with a bar when selected."""

    def __init__(self, text: str, icon_name: str | None = None, dot: str | None = None, on_band: bool = False,
                 parent=None) -> None:
        """`on_band`: sits on a sidebar that takes the header band's color when a theme pack sets one."""
        super().__init__(parent)
        self._on_band = on_band
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(text)
        self._icon_name, self._dot, self._count = icon_name, dot, ""

    def set_count(self, count: int | None) -> None:
        self._count = "" if count is None else str(count)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(212, 34)

    def _colors(self, t) -> dict:
        """On a branded (colored) sidebar, draw with the header band's colors."""
        h, pal = t.header, t.palette
        if h.branded and self._on_band:
            return {"text": h.text, "text2": h.text2, "hover": h.hover, "select": h.button_border,
                    "bar": h.primary_bg, "icon_on": h.text}
        return {"text": pal.text, "text2": pal.text3, "hover": pal.hover, "select": pal.select,
                "bar": t.accent.fill, "icon_on": t.accent.text}

    def paintEvent(self, _e) -> None:  # noqa: N802 - Qt API
        t = theme.current()
        c = self._colors(t)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        if self.isChecked() or self.underMouse():
            p.setPen(Qt.NoPen)
            p.setBrush(to_qcolor(c["select"] if self.isChecked() else c["hover"]))
            p.drawRoundedRect(r, 6, 6)
        if self.isChecked():
            p.setBrush(to_qcolor(c["bar"]))
            p.drawRoundedRect(QRectF(0, r.height() / 2 - 8, 3, 16), 1.5, 1.5)
        if self._dot:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(self._dot))
            p.drawEllipse(QRectF(15, r.height() / 2 - 5, 10, 10))
        elif self._icon_name:
            color = c["icon_on"] if self.isChecked() else c["text2"]
            icon(self._icon_name, color, color, color).paint(p, 12, int(r.height() / 2 - 9), 18, 18)
        font = self.font()
        font.setBold(self.isChecked())
        p.setFont(font)
        p.setPen(to_qcolor(c["text"]))
        p.drawText(r.adjusted(40, 0, -40, 0), Qt.AlignLeft | Qt.AlignVCenter, self.text())
        if self._count:
            font.setBold(False)
            p.setFont(font)
            p.setPen(to_qcolor(c["text2"]))
            p.drawText(r.adjusted(0, 0, -10, 0), Qt.AlignRight | Qt.AlignVCenter, self._count)
        if self.hasFocus():
            p.setPen(QColor(t.accent.fill))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 6, 6)
        p.end()

    def enterEvent(self, e) -> None:  # noqa: N802 - Qt API
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802 - Qt API
        self.update()
        super().leaveEvent(e)


class SettingRow(QFrame):
    """A Windows 11 style settings card: title and description on the left, a control on the right."""

    def __init__(self, title: str, description: str = "", control: QWidget | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingCard")
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = QLabel(title)
        self.title.setObjectName("SettingTitle")
        self.title.setWordWrap(True)
        text.addWidget(self.title)
        self.description = QLabel(description)
        self.description.setProperty("sfRole", "muted")
        self.description.setWordWrap(True)
        self.description.setVisible(bool(description))
        text.addWidget(self.description)
        row.addLayout(text, 1)
        self.control = control
        if control is not None:
            row.addWidget(control, 0, Qt.AlignVCenter)
            if not control.accessibleName():
                control.setAccessibleName(title)

    def set_description(self, text: str) -> None:
        self.description.setText(text)
        self.description.setVisible(bool(text))


def stack(*widgets: QWidget, spacing: int = 4) -> QWidget:
    """Settings cards one above another."""
    box = QWidget()
    col = QVBoxLayout(box)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(spacing)
    for widget in widgets:
        col.addWidget(widget)
    return box
