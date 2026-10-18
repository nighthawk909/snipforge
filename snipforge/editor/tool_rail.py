"""The editor's tool rail: every tool always has its own button.

One column when the window is tall enough, and two or three when it isn't, so no tool ever hides
behind an overflow arrow. The rail's minimum height is the three-column layout, so the window can't
get too short to show them all.

From the keyboard the rail is one Tab stop, the tool you're on (as in a Windows toolbar): the arrow
keys, Home and End move between tools, and Enter or Space picks one and goes to the canvas
(`picked`), as Esc does without picking. Clicking a tool never takes the keyboard from the canvas,
where each tool has its own key.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QSizePolicy, QToolButton, QWidget

ICON_SIZE = 20
PAD_X, PAD_Y = 6, 8  # around the buttons
BORDER = 1  # the rail's right edge line (styles.py)
SPACING = 2  # between buttons
DIVIDER_GAP = 6  # above and below the line between groups
DIVIDER_INSET = 10  # the line is shorter than the buttons
MAX_COLUMNS = 3
BACK = (Qt.Key_Up, Qt.Key_Left)
ON = (Qt.Key_Down, Qt.Key_Right)
PICK = (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space)


class ToolRail(QWidget):
    picked = Signal()  # Enter, Space or Esc on a tool: the keyboard goes back to the canvas

    def __init__(self, groups: list[list[tuple[str, QAction]]], parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ToolRail")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.setAccessibleName("Tools")
        self._buttons: dict[str, QToolButton] = {}
        self._groups = [[self._make_button(name, action) for name, action in group] for group in groups if group]
        self._dividers = [self._make_divider() for _ in self._groups[1:]]
        self._columns = 1
        checked = next((n for n, b in self._buttons.items() if b.defaultAction().isChecked()), None)
        self._set_stop(checked or next(iter(self._buttons)))

    def _make_button(self, name: str, action: QAction) -> QToolButton:
        button = QToolButton(self)
        button.setDefaultAction(action)
        button.setAccessibleName(action.text())
        button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.NoFocus)  # never on a click; one of them is the rail's Tab stop (_set_stop)
        button.setCursor(Qt.PointingHandCursor)
        button.installEventFilter(self)
        action.toggled.connect(lambda on, n=name: on and self._set_stop(n))
        self._buttons[name] = button
        return button

    # ------------------------------------------------------------------ keyboard
    def _set_stop(self, name: str) -> None:
        """Make `name`'s button the one Tab reaches."""
        for other, button in self._buttons.items():
            button.setFocusPolicy(Qt.TabFocus if other == name else Qt.NoFocus)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt API
        if event.type() != QEvent.KeyPress or obj not in self._buttons.values():
            return False
        key, order = event.key(), list(self._buttons.values())
        n = order.index(obj)
        if key in BACK + ON + (Qt.Key_Home, Qt.Key_End):
            n = {Qt.Key_Home: 0, Qt.Key_End: len(order) - 1}.get(key, n + (1 if key in ON else -1))
            target = order[max(0, min(len(order) - 1, n))]
            self._set_stop(next(name for name, b in self._buttons.items() if b is target))
            target.setFocus(Qt.TabFocusReason)
            return True
        if key in PICK:
            obj.click()
            self.picked.emit()
            return True
        if key == Qt.Key_Escape:
            self.picked.emit()
            return True
        return False

    def _make_divider(self) -> QWidget:
        line = QWidget(self)
        line.setObjectName("RailDivider")
        line.setAttribute(Qt.WA_StyledBackground, True)
        return line

    # ------------------------------------------------------------------ queries
    def button(self, name: str) -> QToolButton:
        return self._buttons[name]

    def columns(self) -> int:
        return self._columns

    # ------------------------------------------------------------------ geometry
    def _cell(self) -> int:
        """Every button is the same square, big enough for the largest one's hint."""
        return max(max(b.sizeHint().width(), b.sizeHint().height()) for b in self._buttons.values())

    @staticmethod
    def _rows(group: list[QToolButton], columns: int) -> int:
        return -(-len(group) // columns)

    def _height_for(self, columns: int) -> int:
        cell = self._cell()
        rows = [self._rows(group, columns) for group in self._groups]
        buttons = sum(rows) * cell + (sum(rows) - len(rows)) * SPACING
        return PAD_Y * 2 + buttons + len(self._dividers) * (DIVIDER_GAP * 2 + 1)

    def _width_for(self, columns: int) -> int:
        return PAD_X * 2 + BORDER + columns * self._cell() + (columns - 1) * SPACING

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(self._width_for(self._columns), self._height_for(1))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(self._width_for(self._columns), self._height_for(MAX_COLUMNS))

    def resizeEvent(self, e) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(e)
        self._relayout()

    def changeEvent(self, e) -> None:  # noqa: N802 - Qt API
        super().changeEvent(e)
        if e.type() == QEvent.StyleChange:  # a new theme can change the buttons' size
            self.updateGeometry()
            self._relayout()

    def _relayout(self) -> None:
        """The fewest columns that fit the rail's height."""
        columns = next((n for n in range(1, MAX_COLUMNS) if self.height() >= self._height_for(n)), MAX_COLUMNS)
        if columns != self._columns:
            self._columns = columns
            self.updateGeometry()  # the window gives the rail its new width
        self._place()

    def _place(self) -> None:
        cell, columns = self._cell(), self._columns
        width = columns * cell + (columns - 1) * SPACING
        y = PAD_Y
        for n, group in enumerate(self._groups):
            if n:
                y += DIVIDER_GAP
                self._dividers[n - 1].setGeometry(QRect(PAD_X + DIVIDER_INSET, y, width - DIVIDER_INSET * 2, 1))
                y += 1 + DIVIDER_GAP
            for i, button in enumerate(group):
                row, col = divmod(i, columns)
                button.setGeometry(PAD_X + col * (cell + SPACING), y + row * (cell + SPACING), cell, cell)
            rows = self._rows(group, columns)
            y += rows * cell + (rows - 1) * SPACING
