"""Your own quick styles: save the selected markup's look for its tool, and apply it in one click.

Saved per tool (an arrow's styles show for arrows), kept in the app's settings so they're there after
a restart, and shared by every open editor. The built-in quick styles (quick_styles.py) never change.
"""
from __future__ import annotations

import json
import logging

from PySide6.QtCore import QObject, Signal

from ..settings import app_settings
from .style import STYLE_FIELDS, Style

log = logging.getLogger(__name__)

SETTING = "saved_quick_styles"
MAX_SAVED = 8  # per tool: two rows of tiles


class _Notifier(QObject):
    changed = Signal()


_notifier: _Notifier | None = None


def notifier() -> _Notifier:
    """`notifier().changed` fires whenever any editor saves or deletes a quick style. Connect a bound
    method of a QObject so the connection ends when that object is destroyed."""
    global _notifier
    if _notifier is None:
        _notifier = _Notifier()
    return _notifier


def fields_of(kind: str, style: Style) -> dict:
    """The parts of `style` that `kind` shows: what a saved quick style keeps and applies. A hollow
    look leaves out the fill's color and opacity, which only show once Fill is on."""
    found = {name: getattr(style, name) for name in STYLE_FIELDS.get(kind, ())}
    if not found.get("fill", True):
        found.pop("fill_color", None)
        found.pop("fill_opacity", None)
    return found


def same_look(kind: str, a: Style, b: Style) -> bool:
    """Whether two styles look the same on `kind` (#AABBCC is #aabbcc)."""
    def key(style: Style) -> dict:
        found = fields_of(kind, style)
        if "color" in found:
            found["color"] = found["color"].lower()
        return found
    return key(a) == key(b)


def can_save(kind: str) -> bool:
    return bool(STYLE_FIELDS.get(kind))


def _raw() -> dict:
    """The setting as stored; {} if it isn't a JSON object."""
    raw = app_settings().value(SETTING, "")
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw else {}
    except (ValueError, RecursionError):
        log.warning("Ignoring unreadable saved quick styles")
        return {}
    return data if isinstance(data, dict) else {}


def load() -> dict[str, list[Style]]:
    """Saved quick styles by tool. Values of the wrong type read as the default (Style.from_dict);
    entries that aren't style objects, and tools this version doesn't have, are skipped."""
    out: dict[str, list[Style]] = {}
    for kind, entries in _raw().items():
        if can_save(kind) and isinstance(entries, list):
            styles = [Style.from_dict(e) for e in entries[:MAX_SAVED] if isinstance(e, dict)]
            if styles:
                out[kind] = styles
    return out


def saved_for(kind: str) -> list[Style]:
    return load().get(kind, [])


def _store(kind: str, styles: list[Style]) -> None:
    """Rewrite one tool's list. Other tools' entries stay exactly as stored, including ones a newer
    SnipForge saved that this version can't read."""
    data = {k: v for k, v in _raw().items() if k != kind}
    if styles:
        data[kind] = [fields_of(kind, s) for s in styles]
    app_settings().setValue(SETTING, json.dumps(data))
    notifier().changed.emit()


def save(kind: str, style: Style) -> bool:
    """Add `style` to `kind`'s quick styles. False if the tool has nothing to save, it's already
    there, or the tool has MAX_SAVED already."""
    if not can_save(kind):
        return False
    styles = saved_for(kind)
    if any(same_look(kind, s, style) for s in styles) or len(styles) >= MAX_SAVED:
        return False
    _store(kind, [*styles, Style().with_changes(**fields_of(kind, style))])
    return True


def delete(kind: str, style: Style) -> None:
    """Remove the saved style that looks like `style` (by look, not position, so another editor's
    change in between can't make it remove a different one)."""
    styles = saved_for(kind)
    kept = [s for s in styles if not same_look(kind, s, style)]
    if len(kept) != len(styles):
        _store(kind, kept)
