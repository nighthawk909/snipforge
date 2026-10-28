"""Colors you've used and your own palette.

`recent()` is the last few colors picked by hand (the custom color dialog, the eyedropper or the hex
box), newest first, so they're one click away. `palette()` is the panel's eight swatches: the usual
ones until you change any (right-click a swatch); `reset_palette` puts them back. Both are kept in
the app's settings and shared by every open editor (`notifier`).
"""
from __future__ import annotations

import json
import re

from PySide6.QtCore import QObject, Signal

from ..settings import app_settings
from .style import PALETTE as DEFAULT_PALETTE  # noqa: F401 - re-exported: the usual swatches

RECENT_SETTING = "editor/recent_colors"
PALETTE_SETTING = "editor/palette"
MAX_RECENT = 8
_HEX = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")


def normalize(text: str) -> str | None:
    """"#rrggbb" in lower case for a hex color written any usual way ("ABC", "#AABBCC"), else None."""
    match = _HEX.match((text or "").strip())
    if not match:
        return None
    digits = match.group(1).lower()
    if len(digits) == 3:
        digits = "".join(d * 2 for d in digits)
    return "#" + digits


def _read_list(key: str, unique: bool = True) -> list[str]:
    raw = app_settings().value(key, "")
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw else []
    except (ValueError, RecursionError):
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        color = normalize(item) if isinstance(item, str) else None
        if color and (not unique or color not in out):
            out.append(color)
    return out


def recent() -> list[str]:
    return _read_list(RECENT_SETTING)[:MAX_RECENT]


def remember(color: str) -> None:
    """A color picked by hand: first in Recent, each color once."""
    normalized = normalize(color)
    if normalized is None:
        return
    found = [normalized, *(c for c in recent() if c != normalized)][:MAX_RECENT]
    app_settings().setValue(RECENT_SETTING, json.dumps(found))
    notifier().changed.emit()


def palette() -> list[str]:
    """The panel's swatches: the usual eight, with any you've replaced."""
    stored = _read_list(PALETTE_SETTING, unique=False)
    if len(stored) != len(DEFAULT_PALETTE):
        return list(DEFAULT_PALETTE)
    return stored


def set_palette_color(index: int, color: str) -> None:
    normalized = normalize(color)
    if normalized is None or not 0 <= index < len(DEFAULT_PALETTE):
        return
    swatches = palette()
    swatches[index] = normalized
    app_settings().setValue(PALETTE_SETTING, json.dumps(swatches))
    notifier().changed.emit()


def reset_palette() -> None:
    app_settings().remove(PALETTE_SETTING)
    notifier().changed.emit()


class _Notifier(QObject):
    changed = Signal()


_notifier: _Notifier | None = None


def notifier() -> _Notifier:
    global _notifier
    if _notifier is None:
        _notifier = _Notifier()
    return _notifier
