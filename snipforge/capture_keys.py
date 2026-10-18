"""Choosing a window to snip without a mouse: in Window, Area and Scrolling modes the arrow keys go
from window to window (Area and Scrolling end with the whole monitor, as hovering the desktop
does), the one chosen is shown as the pointer would show it, and Enter snips it. Moving the pointer
or changing the mode hands the choice back. Mixed into the capture overlay (capture.py), which keeps
Tab for its toolbar."""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt

KEYS_ON = (Qt.Key_Right, Qt.Key_Down)
KEYS_BACK = (Qt.Key_Left, Qt.Key_Up)
CHOOSING_MODES = ("window", "area", "scroll")


class WindowKeys:
    """For _ScreenOverlay: uses its mode, window list, hover, dragging state and _emit. Its
    keyPressEvent asks _window_key first, since Enter otherwise means the whole monitor. (No event()
    override: that would run Python for every event the overlay gets.)"""

    _key_choice: tuple[str, int] | None = None  # (the mode it was made in, which of _choices())

    def _describe_keys(self, presets: bool) -> None:
        """The overlay's name and keys, for screen readers."""
        self.setAccessibleName("Choose what to snip")
        self.setAccessibleDescription(
            "A to drag an area, W for a window (arrow keys go from window to window), F for the full screen, "
            "X for a fixed size frame (arrow keys move it, Enter snips it, X again picks its size), "
            + ("S to scroll a long page or list, " if self._scrolling else "")
            + "Enter for the whole monitor, D for a delay, P to include the pointer, "
            + ("1 to 9 for a capture preset and 0 for your usual choices, " if presets else "") + "Esc to cancel")

    def _window_key(self, e) -> bool:
        """True if `e` chose a window or snipped the one chosen (a moved pointer changes what's shown,
        and so what Enter snips: _chosen checks)."""
        plain = e.modifiers() in (Qt.NoModifier, Qt.KeypadModifier)
        if self._mode not in CHOOSING_MODES or self._press is not None or not plain:
            return False
        if e.key() in KEYS_ON + KEYS_BACK and self._choices():
            self._choose_by_key(1 if e.key() in KEYS_ON else -1)
            return True
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            choice = self._chosen()
            self._key_choice = None
            if choice is not None:
                self._emit(choice)
                return True
        return False

    def _choices(self) -> list[QRect]:
        """What the arrow keys go through: the windows, and the monitor last except in Window mode."""
        return list(self._windows[:-1] if self._mode == "window" else self._windows)

    def _chosen(self) -> QRect | None:
        """The window the arrow keys chose, if it's still what's shown (same mode, still highlighted)."""
        if self._key_choice is None:
            return None
        mode, n = self._key_choice
        choices = self._choices()
        if mode != self._mode or not 0 <= n < len(choices) or self._hover != choices[n]:
            return None
        return QRect(choices[n])

    def _choose_by_key(self, step: int) -> None:
        choices = self._choices()
        current = self._key_choice[1] if self._chosen() is not None else None
        n = (0 if step > 0 else len(choices) - 1) if current is None else (current + step) % len(choices)
        self._key_choice = (self._mode, n)
        self._hover = QRect(choices[n])
        self.update()
