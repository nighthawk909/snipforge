"""The toolbar at the top of the capture overlay: how to choose what to snip (and, in Fixed size
mode, the size), a delay, whether the mouse pointer is included, and Cancel.

Its buttons never take keyboard focus from the overlay when clicked (Tab still reaches them, and
Enter or Space presses the focused one), so Esc, Enter and the mode letters keep working however
the toolbar was used. Clicks on the toolbar never reach the screenshot behind it; a right-click
cancels, as it does anywhere else on the overlay.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSpinBox,
    QToolButton,
    QWidget,
    QWidgetAction,
)

from .icons import icon
from .prefs import DEFAULT_FIXED_SIZE, DELAYS, MAX_FIXED, MIN_FIXED


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
    "fixed": CaptureMode("Fixed size", "fixed", "X", "A frame of an exact size: move it and click",
                         "Click to snip the frame  •  arrow keys nudge it  •  X again for its size"
                         "  •  Esc to cancel"),
    "scroll": CaptureMode("Scrolling", "scroll", "S", "A long page or list, captured as one image",
                          "Drag over the part that scrolls, or click a window  •  SnipForge scrolls to the end"
                          "  •  move the mouse to stop  •  Esc to cancel"),
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


def size_label(width: int, height: int) -> str:
    return f"{width} × {height}"


# Fixed size mode's menu: (width, height, what it's good for). Sizes are screen pixels.
FIXED_PRESETS = (
    (640, 480, ""),
    (800, 600, ""),
    (1024, 768, ""),
    (1280, 720, "HD"),
    (1920, 1080, "Full HD"),
    (1080, 1080, "square"),
)


class _EnterButton(QPushButton):
    """Enter presses it when it has the keyboard (a QPushButton only does that in a dialog)."""

    def keyPressEvent(self, e) -> None:  # noqa: N802 - Qt API
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.click()
            e.accept()
            return
        super().keyPressEvent(e)


class _CustomSize(QWidget):
    """The last row of the size menu: type a width and height, then Use (or Enter)."""

    picked = Signal(int, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 12, 8)
        row.setSpacing(6)
        row.addWidget(QLabel("Custom"))
        self.width_box = self._box("Width in pixels")
        self.height_box = self._box("Height in pixels")
        row.addWidget(self.width_box)
        row.addWidget(QLabel("×"))
        row.addWidget(self.height_box)
        self.use_button = _EnterButton("Use")
        self.use_button.setAccessibleName("Use this size")
        self.use_button.clicked.connect(self._pick)
        row.addWidget(self.use_button)
        # Arrowing onto this row in the menu puts the keyboard in the width box (Tab: the height).
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFocusProxy(self.width_box)

    def _box(self, name: str) -> QSpinBox:
        box = QSpinBox(self)
        box.setRange(MIN_FIXED, MAX_FIXED)
        box.setCorrectionMode(QSpinBox.CorrectToNearestValue)  # "5" means the smallest, not what was there
        box.setAccessibleName(name)
        box.setToolTip(name)
        box.lineEdit().returnPressed.connect(self._pick)
        return box

    def set_size(self, width: int, height: int) -> None:
        self.width_box.setValue(width)
        self.height_box.setValue(height)

    def focusNextPrevChild(self, forward: bool) -> bool:  # noqa: N802 - Qt API
        """Tab and Shift+Tab go round the boxes and Use, instead of on to the menu's other rows."""
        chain = [self.width_box, self.height_box, self.use_button]
        focused = self.window().focusWidget()
        if focused not in chain:
            return super().focusNextPrevChild(forward)
        chain[(chain.index(focused) + (1 if forward else -1)) % len(chain)].setFocus(Qt.TabFocusReason)
        return True

    def reset(self, width: int, height: int) -> None:
        """For the menu opening again: the current size, and the keyboard out of the boxes (so the
        arrow keys move through the menu rather than changing a number)."""
        self.set_size(width, height)
        focused = self.window().focusWidget()
        if focused is not None and self.isAncestorOf(focused):
            focused.clearFocus()

    def _pick(self) -> None:
        for box in (self.width_box, self.height_box):
            box.interpretText()  # what's typed, even if Enter came before the box finished with it
        self.picked.emit(self.width_box.value(), self.height_box.value())


class _DelayButton(_ToolbarButton):
    """Opens the Delay menu; its highlight says whether a delay is on, so opening and closing the
    menu must not toggle it (set_delay is the only thing that does)."""

    def nextCheckState(self) -> None:  # noqa: N802 - Qt API
        pass


class CaptureToolbar(QFrame):
    mode_picked = Signal(str)
    size_picked = Signal(int, int)  # Fixed size mode's frame, in screen pixels
    delay_picked = Signal(int)  # seconds; picking one (other than none) starts the countdown
    pointer_toggled = Signal(bool)
    cancel_clicked = Signal()

    def __init__(self, mode: str, parent=None, *, delay: int = 0, pointer: bool = False,
                 pointer_available: bool = True, pointer_why: str = "", scrolling: bool = True,
                 fixed_size: tuple[int, int] = DEFAULT_FIXED_SIZE) -> None:
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
            if key == "scroll" and not scrolling:
                continue  # can't scroll other windows here (yet): don't offer it
            button = self._button(spec.icon, f" {spec.label}", f"{spec.tip} ({spec.key})")  # space: icon gap
            button.setAccessibleName(spec.label)
            button.setAccessibleDescription(f"{spec.tip}. Shortcut: {spec.key}")
            button.setCheckable(True)
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            button.clicked.connect(lambda _=False, k=key: self.mode_picked.emit(k))
            self._group.addButton(button)
            self.buttons[key] = button
            row.addWidget(button)
        self._size_separator = self._separator()
        self._size_shown = True  # the size button (only in Fixed size mode)
        row.addWidget(self._size_separator)
        row.addWidget(self._size_control())
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
        self.set_fixed_size(*fixed_size)
        self.set_mode(mode)
        self.set_delay(delay)
        self.set_pointer(pointer)
        self.set_pointer_available(pointer_available, pointer_why)

    def _size_control(self) -> QToolButton:
        """Fixed size mode's size: presets, and a row to type any size. Shown only in that mode."""
        self.size_button = self._button("chevron_down", "", "The frame's size, in screen pixels")
        self.size_button.setObjectName("SizeButton")
        self.size_button.setAccessibleName("Size")
        self.size_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.size_button.setLayoutDirection(Qt.RightToLeft)  # the arrow after the size
        self.size_button.setPopupMode(QToolButton.InstantPopup)
        self.size_button.setText(size_label(MAX_FIXED, MAX_FIXED))  # as wide as it gets, so a new size
        self.size_button.setFixedWidth(self.size_button.sizeHint().width())  # never moves the buttons
        self.size_menu = QMenu(self.size_button)
        self.size_menu.setLayoutDirection(Qt.LeftToRight)  # not the button's right-to-left
        self.size_menu.setAccessibleName("Size")
        group = QActionGroup(self.size_menu)
        group.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)  # a custom size is none of them
        self._size_actions = {}
        for width, height, note in FIXED_PRESETS:
            action = self.size_menu.addAction(size_label(width, height) + (f"  ({note})" if note else ""))
            action.setCheckable(True)
            group.addAction(action)
            action.triggered.connect(lambda _=False, w=width, h=height: self.size_picked.emit(w, h))
            self._size_actions[(width, height)] = action
        self.size_menu.addSeparator()
        self.custom_size = _CustomSize(self.size_menu)
        self.custom_size.picked.connect(self._custom_picked)
        custom = QWidgetAction(self.size_menu)
        custom.setDefaultWidget(self.custom_size)
        self.size_menu.addAction(custom)
        self.size_button.setMenu(self.size_menu)
        self.size_menu.aboutToShow.connect(lambda: self.custom_size.reset(*self._fixed_size))
        return self.size_button

    def open_size_menu(self) -> None:
        """Show the size menu under its button (for X in Fixed size mode), on the current size."""
        current = next((a for a in self._size_actions.values() if a.isChecked()), None)
        self.size_menu.popup(self.size_button.mapToGlobal(QPoint(0, self.size_button.height())), current)
        if current is not None:
            self.size_menu.setActiveAction(current)

    def _custom_picked(self, width: int, height: int) -> None:
        self.size_menu.close()
        self.size_picked.emit(width, height)

    def set_fixed_size(self, width: int, height: int) -> None:
        """Show this as Fixed size mode's size, without announcing it."""
        self._fixed_size = (width, height)
        for size, action in self._size_actions.items():
            action.setChecked(size == (width, height))
        self.custom_size.set_size(width, height)
        self.size_button.setText(size_label(width, height))
        self.size_button.setToolTip(f"The frame is {size_label(width, height)} screen pixels. Click to change it")
        if self._size_shown:
            self.adjustSize()

    def widest(self) -> int:
        """Its width with the size button showing. The overlay places the toolbar by this, so
        switching to or from Fixed size never slides a button out from under the pointer."""
        if self._size_shown:
            return self.width()
        gap = self.layout().spacing()
        return self.width() + self.size_button.minimumWidth() + self._size_separator.width() + 2 * gap

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

    def set_compact(self, compact: bool) -> None:
        """Icons only (names stay in tooltips and for screen readers), for monitors too narrow for
        the labels."""
        style = Qt.ToolButtonIconOnly if compact else Qt.ToolButtonTextBesideIcon
        for button in (*self.buttons.values(), self.delay_button):
            button.setToolButtonStyle(style)
        self.adjustSize()

    def set_mode(self, mode: str) -> None:
        """Show `mode` as the chosen one, without announcing it (for changes made elsewhere)."""
        self.buttons[mode].setChecked(True)
        fixed = mode == "fixed"
        if fixed != self._size_shown:
            self._size_shown = fixed
            self.size_button.setVisible(fixed)
            self._size_separator.setVisible(fixed)
            self.adjustSize()

    def mousePressEvent(self, e) -> None:  # noqa: N802 - Qt API
        if e.button() == Qt.RightButton:
            self.cancel_clicked.emit()
        e.accept()  # never a click on the screenshot behind the toolbar

    def event(self, e) -> bool:
        if e.type() == QEvent.MouseButtonDblClick:
            e.accept()
            return True
        return super().event(e)
