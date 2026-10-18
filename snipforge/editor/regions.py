"""F6 and Shift+F6 in the editor: the keyboard goes to the next (or previous) part of the window,
as in Windows apps: the canvas, the properties panel, the command bar and the tool rail. Each part
gets the keyboard where Tab would enter it (the rail: the tool you're on)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

ORDER = ("canvas", "panel", "commands", "tools")


def _parts(window) -> dict[str, QWidget]:
    return {"canvas": window.canvas, "panel": window.panel, "commands": window.commands, "tools": window.tool_rail}


def region_of(window, widget: QWidget | None) -> str | None:
    """Which part of the editor `widget` is in, or None."""
    parts = _parts(window)
    while widget is not None:
        for name, part in parts.items():
            if widget is part:
                return name
        widget = widget.parentWidget()
    return None


def _entry(part: QWidget) -> QWidget | None:
    """Where the keyboard lands in `part`: itself if Tab stops there, else its first Tab stop."""
    if part.focusPolicy() & Qt.TabFocus and part.isVisible() and part.isEnabled():
        return part
    for child in part.findChildren(QWidget):
        if child.focusPolicy() & Qt.TabFocus and child.isVisibleTo(part) and child.isEnabled() \
                and child.focusProxy() is None:
            return child
    return None


def move(window, forward: bool = True) -> None:
    """Give the keyboard to the next part of the editor that can take it."""
    here = region_of(window, QApplication.focusWidget()) or "canvas"
    parts = _parts(window)
    start = ORDER.index(here)
    for step in range(1, len(ORDER) + 1):
        name = ORDER[(start + (step if forward else -step)) % len(ORDER)]
        target = _entry(parts[name])
        if target is not None:
            target.setFocus(Qt.TabFocusReason if forward else Qt.BacktabFocusReason)
            return
