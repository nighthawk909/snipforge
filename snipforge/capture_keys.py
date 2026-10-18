"""Choosing a window to snip without a mouse: in Window, Area and Scrolling modes the arrow keys go
from window to window (Area and Scrolling end with the whole monitor, as hovering the desktop
does), the one chosen is shown as the pointer would show it, and Enter snips it. Moving the pointer
or changing the mode hands the choice back. Mixed into the capture overlay (capture.py), which keeps
Tab for its toolbar."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, Qt

KEYS_ON = (Qt.Key_Right, Qt.Key_Down)
KEYS_BACK = (Qt.Key_Left, Qt.Key_Up)
CHOOSING_MODES = ("window", "area", "scroll")


class WindowKeys:
    """For _ScreenOverlay: uses its mode, window list, hover, dragging state and _emit."""

    _key_choice: tuple[str, int] | None = None  # (the mode it was made in, which of _choices())

    def event(self, e) -> bool:
        """Ahead of the overlay's own key handling, where Enter means the whole monitor."""
        if e.type() == QEvent.MouseMove:
            self._key_choice = None  # the pointer leads again
        elif e.type() == QEvent.KeyPress and self._window_key(e):
            return True
        return super().event(e)

    def _window_key(self, e) -> bool:
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
