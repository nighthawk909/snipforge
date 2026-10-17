"""Scrolling a window for scrolling capture: put the pointer over it and turn the mouse wheel.

Windows sends wheel turns to whatever is under the pointer, so this moves the pointer there and
sends real wheel input, like a person scrolling. The scrolling capture notices when the person
moves the mouse themselves (the pointer isn't where it was put) and stops. Windows only for now:
elsewhere available() is False and the toolbar doesn't offer Scrolling.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QPoint

WHEEL_DELTA = 120  # one notch of a standard mouse wheel


def available() -> bool:
    return sys.platform == "win32"


def pointer_position() -> QPoint:
    """Where the pointer is, in physical screen pixels."""
    from . import _scroll_win32
    return _scroll_win32.cursor_pos()


def place_pointer(at: QPoint) -> None:
    """Move the pointer to `at` (physical screen pixels)."""
    from . import _scroll_win32
    _scroll_win32.set_cursor_pos(at)


def scroll_down(notches: int) -> None:
    """Turn the wheel `notches` clicks toward the user (content moves up), wherever the pointer is."""
    if notches <= 0:
        raise ValueError("scroll by at least one notch")
    from . import _scroll_win32
    _scroll_win32.wheel(-notches * WHEEL_DELTA)
