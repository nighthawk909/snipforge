"""The fonts SnipForge's interface asks for: Segoe UI, and Consolas where text needs a fixed width."""
from __future__ import annotations

from PySide6.QtGui import QFont

WINDOWS_UI = "Segoe UI"
WINDOWS_MONO = "Consolas"


def ui_font(point_size: int) -> QFont:
    """The interface font at `point_size`."""
    return QFont(WINDOWS_UI, point_size)


def mono_font(point_size: int) -> QFont:
    """A fixed-width font at `point_size`."""
    return QFont(WINDOWS_MONO, point_size)
