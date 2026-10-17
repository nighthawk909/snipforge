"""The toolbar at the top of the capture overlay: how to choose what to snip, and Cancel.

Its buttons never take keyboard focus from the overlay when clicked (Tab still reaches them), so
Esc, Enter and the mode letters keep working however the toolbar was used.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QToolButton

from .icons import icon


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


class CaptureToolbar(QFrame):
    mode_picked = Signal(str)
    cancel_clicked = Signal()

    def __init__(self, mode: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("CaptureToolbar")
        self.setAccessibleName("Capture")
        self.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 6, 6, 6)
        row.setSpacing(4)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: dict[str, QToolButton] = {}
        for key, spec in MODES.items():
            button = self._button(spec.icon, f" {spec.label}", f"{spec.tip} ({spec.key})")  # space: icon gap
            button.setAccessibleName(spec.label)
            button.setCheckable(True)
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            button.clicked.connect(lambda _=False, k=key: self.mode_picked.emit(k))
            self._group.addButton(button)
            self.buttons[key] = button
            row.addWidget(button)
        row.addWidget(self._separator())
        self.cancel_button = self._button("close", "", "Cancel (Esc)")
        self.cancel_button.setAccessibleName("Cancel")
        self.cancel_button.clicked.connect(self.cancel_clicked)
        row.addWidget(self.cancel_button)
        self.set_mode(mode)

    def _button(self, icon_name: str, text: str, tip: str) -> QToolButton:
        button = QToolButton(self)
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
