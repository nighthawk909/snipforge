"""The system's high-contrast setting: Windows' contrast themes (Settings > Accessibility > Contrast
themes). While it's on, SnipForge draws its windows in the system's own colors (or in black and
white, if those can't be read), and the light/dark and accent choices wait until it's off. Qt
reports the setting; the colors come from _contrast_win32.py. Snips and markup keep their colors:
they're your content.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from .tokens import READABLE, contrast_ratio

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SystemColors:
    """The few colors a contrast theme is made of, each '#RRGGBB'."""
    window: str          # backgrounds
    text: str            # text, icons and borders on them
    highlight: str       # selection, focus, checked controls
    highlight_text: str  # text on highlight
    button: str
    button_text: str
    disabled: str        # text of controls you can't use
    link: str            # accent-colored text


# Black and white, if Windows' colors can't be read (and off Windows).
DARK =SystemColors(window="#000000", text="#FFFFFF", highlight="#1AEBFF", highlight_text="#000000",
                    button="#000000", button_text="#FFFFFF", disabled="#A6A6A6", link="#FFFF00")
LIGHT = SystemColors(window="#FFFFFF", text="#000000", highlight="#37006E", highlight_text="#FFFFFF",
                     button="#FFFFFF", button_text="#000000", disabled="#600000", link="#00009F")


def high() -> bool:
    """Whether the system asks apps for more contrast."""
    try:
        hints = QGuiApplication.styleHints().accessibility()
        return hints.contrastPreference() == Qt.ContrastPreference.HighContrast
    except (AttributeError, RuntimeError):  # no application yet, or a Qt without the setting
        return False


def system_colors(dark: bool) -> SystemColors:
    """The colors to draw with while high contrast is on: Windows' own, else black and white (and
    black and white too if Windows' text can't be read on its background)."""
    if sys.platform == "win32":
        from . import _contrast_win32
        try:
            colors = _contrast_win32.colors()
            if contrast_ratio(colors.text, colors.window) >= READABLE:
                return colors
            log.warning("Windows' contrast colors %s on %s can't be read; using black and white",
                        colors.text, colors.window)
        except (OSError, ValueError) as exc:
            log.warning("Couldn't read Windows' contrast colors (%s); using black and white", exc)
    return DARK if dark else LIGHT


def on_change(callback) -> object | None:
    """Call `callback()` when the setting changes; returns what to pass to stop(), or None."""
    try:
        hints = QGuiApplication.styleHints().accessibility()
        hints.contrastPreferenceChanged.connect(callback)
        return hints
    except (AttributeError, RuntimeError):
        return None


def stop(hints, callback) -> None:
    if hints is None:
        return
    try:
        hints.contrastPreferenceChanged.disconnect(callback)
    except (RuntimeError, TypeError):
        pass
