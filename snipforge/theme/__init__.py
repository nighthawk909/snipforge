"""SnipForge theming: design tokens, theme packs and the app-wide theme manager.

main() calls install() once. Everything else reads current() when it paints, and widgets that
draw their own colors connect a bound method with on_change() to repaint when the theme changes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .tokens import BUILTIN_ACCENTS, DEFAULT_ACCENT, Theme, resolve

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

    from .manager import ThemeManager

_DEFAULT = resolve("light", BUILTIN_ACCENTS[DEFAULT_ACCENT])
_manager: ThemeManager | None = None


def install(app: QApplication) -> ThemeManager:
    global _manager
    if _manager is None:
        from .manager import ThemeManager
        from .styles import ui_font

        app.setStyle("Fusion")
        app.setFont(ui_font())
        _manager = ThemeManager(app)
        _manager.apply()
    return _manager


def get_manager() -> ThemeManager | None:
    return _manager


def current() -> Theme:
    return _manager.theme if _manager is not None else _DEFAULT


def on_change(slot) -> None:
    """Call `slot(theme)` after every theme change. Pass a bound method of a QObject so the
    connection ends when that object is destroyed."""
    if _manager is not None:
        _manager.changed.connect(slot)
