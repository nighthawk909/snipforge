"""The toolbar at the top of the capture overlay: how to choose what to snip, a delay, whether the
mouse pointer is included, and Cancel.

Its buttons never take keyboard focus from the overlay when clicked (Tab still reaches them, and
Enter or Space presses the focused one), so Esc, Enter and the mode letters keep working however
the toolbar was used. Clicks on the toolbar never reach the screenshot behind it; a right-click
cancels, as it does anywhere else on the overlay.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QMenu, QToolButton

from .icons import icon
from .prefs import DELAYS


@dataclass(frozen=True)
class CaptureMode:
    label: str
    icon: str
    key: str  # the letter that switches to it
    tip: str
    hint: str  # shown at the bottom of the screen while choosing


MODES = {
    "area": CaptureMode("Area", "area", "A", "Drag to choose an area, or click a window",
                        "Drag to choose an area  •  click a window  •  Enter for the whole screen  •  Esc to cancel"),
    "window": CaptureMode("Window", "window", "W", "Click a window",
                          "Click a window  •  Enter for the whole screen  •  Esc to cancel"),
    "screen": CaptureMode("Full screen", "monitor", "F", "Click a monitor to take all of it",
                          "Click a monitor, or press Enter  •  Esc to cancel"),
}


class _ToolbarButton(QToolButton):
    """Enter presses a focused button (QToolButton only knows Space), instead of falling through
    to the overlay, where Enter snips the whole monitor."""

    def keyPressEvent(self, e) -> None:  # noqa: N802 - Qt API
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.click()
            e.accept()
            return
        super().keyPressEvent(e)

    def mousePressEvent(self, e) -> None:  # noqa: N802 - Qt API
        if e.button() == Qt.RightButton:
            e.ignore()  # the toolbar turns it into Cancel
            return
        super().mousePressEvent(e)


def delay_label(seconds: int) -> str:
    return "No delay" if seconds == 0 else f"{seconds} s delay"


class _DelayButton(_ToolbarButton):
    """Opens the Delay menu; its highlight says whether a delay is on, so opening and closing the
    menu must not toggle it (set_delay is the only thing that does)."""

    def nextCheckState(self) -> None:  # noqa: N802 - Qt API
        pass


class CaptureToolbar(QFrame):
    mode_picked = Signal(str)
    delay_picked = Signal(int)  # seconds; picking one (other than none) starts the countdown
    pointer_toggled = Signal(bool)
    cancel_clicked = Signal()

    def __init__(self, mode: str, parent=None, *, delay: int = 0, pointer: bool = False,
                 pointer_available: bool = True, pointer_why: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("CaptureToolbar")
        self.setAccessibleName("Capture")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_NoMousePropagation, True)  # its gaps and border aren't the screenshot
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 6, 6, 6)
        row.setSpacing(4)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: dict[str, QToolButton] = {}
        for key, spec in MODES.items():
            button = self._button(spec.icon, f" {spec.label}", f"{spec.tip} ({spec.key})")  # space: icon gap
            button.setAccessibleName(spec.label)
            button.setAccessibleDescription(f"{spec.tip}. Shortcut: {spec.key}")
            button.setCheckable(True)
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            button.clicked.connect(lambda _=False, k=key: self.mode_picked.emit(k))
            self._group.addButton(button)
            self.buttons[key] = button
            row.addWidget(button)
        row.addWidget(self._separator())
        row.addWidget(self._delay_control())
        self.pointer_button = self._button("select", "", "Include the mouse pointer (P)")
        self.pointer_button.setAccessibleName("Include the mouse pointer")
        self.pointer_button.setAccessibleDescription("Draws the pointer into the snip. Shortcut: P")
        self.pointer_button.setCheckable(True)
        self.pointer_button.clicked.connect(self.pointer_toggled)
        row.addWidget(self.pointer_button)
        row.addWidget(self._separator())
        self.cancel_button = self._button("close", "", "Cancel (Esc)")
        self.cancel_button.setAccessibleName("Cancel")
        self.cancel_button.clicked.connect(self.cancel_clicked)
        row.addWidget(self.cancel_button)
        self.set_mode(mode)
        self.set_delay(delay)
        self.set_pointer(pointer)
        self.set_pointer_available(pointer_available, pointer_why)

    def _delay_control(self) -> QToolButton:
        self.delay_button = self._button("timer", "", "Wait before freezing the screen, to snip menus and tooltips (D)",
                                         kind=_DelayButton)
        self.delay_button.setObjectName("DelayButton")
        self.delay_button.setAccessibleName("Delay")
        self.delay_button.setAccessibleDescription("Count down before the screen freezes. Shortcut: D")
        self.delay_button.setCheckable(True)
        self.delay_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.delay_button.setPopupMode(QToolButton.InstantPopup)
        self.delay_menu = QMenu(self.delay_button)
        self.delay_menu.setAccessibleName("Delay")
        group = QActionGroup(self.delay_menu)
        group.setExclusive(True)
        self._delay_actions = {}
        for seconds in DELAYS:
            action = self.delay_menu.addAction("No delay" if seconds == 0 else f"{seconds} seconds")
            action.setCheckable(True)
            group.addAction(action)
            action.triggered.connect(lambda _=False, s=seconds: self.delay_picked.emit(s))
            self._delay_actions[seconds] = action
        self.delay_button.setMenu(self.delay_menu)
        return self.delay_button

    def open_delay_menu(self) -> None:
        """Show the Delay menu under its button (for the D key), on the current choice."""
        current = next((a for a in self.delay_menu.actions() if a.isChecked()), None)
        self.delay_menu.popup(self.delay_button.mapToGlobal(QPoint(0, self.delay_button.height())), current)
        if current is not None:
            self.delay_menu.setActiveAction(current)

    def set_delay(self, seconds: int) -> None:
        """Show `seconds` as the chosen delay, without announcing it."""
        self._delay_actions[seconds].setChecked(True)
        self.delay_button.setText(f" {delay_label(seconds)}")
        self.delay_button.setChecked(seconds > 0)

    def set_pointer(self, on: bool) -> None:
        self.pointer_button.setChecked(on)

    def set_pointer_available(self, available: bool, why: str = "") -> None:
        """No pointer image (it was hidden, or not Windows): the toggle can't do anything; `why` says so."""
        self.pointer_button.setEnabled(available)
        self.pointer_button.setToolTip("Include the mouse pointer (P)" if available
                                       else why or "The pointer isn't available")

    def _button(self, icon_name: str, text: str, tip: str, kind=None) -> QToolButton:
        button = (kind or _ToolbarButton)(self)
        button.setIcon(icon(icon_name))
        button.setIconSize(QSize(18, 18))
        button.setText(text)
        button.setToolTip(tip)
        button.setFocusPolicy(Qt.TabFocus)
        button.setCursor(Qt.ArrowCursor)
        return button

    def _separator(self) -> QFrame:
        line = QFrame(self)
        line.setObjectName("CaptureToolbarSeparator")
        line.setFixedSize(1, 22)
        return line

    def set_mode(self, mode: str) -> None:
        """Show `mode` as the chosen one, without announcing it (for changes made elsewhere)."""
        self.buttons[mode].setChecked(True)

    def mousePressEvent(self, e) -> None:  # noqa: N802 - Qt API
        if e.button() == Qt.RightButton:
            self.cancel_clicked.emit()
        e.accept()  # never a click on the screenshot behind the toolbar

    def event(self, e) -> bool:
        if e.type() == QEvent.MouseButtonDblClick:
            e.accept()
            return True
        return super().event(e)
