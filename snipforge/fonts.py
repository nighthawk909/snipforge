"""Fonts that exist on each platform (docs/CROSS-PLATFORM.md). Segoe UI and Consolas are Windows
fonts; a Mac has neither, so it gets its own system font and Menlo instead of whatever Qt falls back
to. Windows keeps exactly the fonts it always had.
"""
from __future__ import annotations

import sys

from PySide6.QtGui import QFont, QFontDatabase

WINDOWS_UI = "Segoe UI"
WINDOWS_MONO = "Consolas"
MAC_MONO = "Menlo"  # macOS's monospaced font since 10.6


def ui_font(point_size: int) -> QFont:
    """The interface font at `point_size`: Segoe UI on Windows, the system font (SF) on a Mac."""
    if sys.platform != "darwin":
        return QFont(WINDOWS_UI, point_size)
    font = QFontDatabase.systemFont(QFontDatabase.GeneralFont)
    font.setPointSize(point_size)
    return font


def mono_font(point_size: int) -> QFont:
    """A fixed-width font at `point_size`: Consolas on Windows, Menlo on a Mac."""
    if sys.platform != "darwin":
        return QFont(WINDOWS_MONO, point_size)
    font = QFont(MAC_MONO, point_size)
    font.setStyleHint(QFont.Monospace)  # whatever fixed-width font there is, should Menlo be missing
    return font
