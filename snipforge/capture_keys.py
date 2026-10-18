"""Choosing a window to snip without a mouse: in Window and Area modes the arrow keys go from
window to window (Area mode ends with the whole monitor, as hovering the desktop does), the one
chosen is shown as the pointer would show it, and Enter snips it. Moving the pointer hands the
choice back to it. Mixed into the capture overlay (capture.py), which keeps Tab for its toolbar."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, Qt

KEYS_ON = (Qt.Key_Right, Qt.Key_Down)
KEYS_BACK = (Qt.Key_Left, Qt.Key_Up)


class WindowKeys:
    """For _ScreenOverlay: uses its mode, window list, hover, dragging state and _emit."""

    _key_choice: int | None = None

    def event(self, e) -> bool:
        """Ahead of the overlay's own key handling, where Enter means the whole monitor."""
        if e.type() == QEvent.MouseMove:
            self._key_choice = None  # the pointer leads again
        elif e.type() == QEvent.KeyPress and self._window_key(e):
            return True
        return super().event(e)

    def _window_key(self, e) -> bool:
        plain = e.modifiers() in (Qt.NoModifier, Qt.KeypadModifier)
        if self._mode not in ("window", "area") or self._press is not None or not plain:
            return False
        if e.key() in KEYS_ON + KEYS_BACK and self._choices():
            self._choose_by_key(1 if e.key() in KEYS_ON else -1)
            return True
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and self._key_choice is not None:
            choice = QRect(self._choices()[self._key_choice])
            self._key_choice = None
            self._emit(choice)
            return True
        return False

    def _choices(self) -> list[QRect]:
        """What the arrow keys go through: the windows, and in Area mode the monitor last."""
        return list(self._windows if self._mode == "area" else self._windows[:-1])

    def _choose_by_key(self, step: int) -> None:
        choices = self._choices()
        if self._key_choice is None or self._key_choice >= len(choices):
            self._key_choice = 0 if step > 0 else len(choices) - 1
        else:
            self._key_choice = (self._key_choice + step) % len(choices)
        self._hover = QRect(choices[self._key_choice])
        self.update()
