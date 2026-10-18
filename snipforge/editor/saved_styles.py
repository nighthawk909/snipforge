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
    """The parts of `style` that `kind` shows: what a saved quick style keeps and applies."""
    return {name: getattr(style, name) for name in STYLE_FIELDS.get(kind, ())}


def can_save(kind: str) -> bool:
    return bool(STYLE_FIELDS.get(kind))


def load() -> dict[str, list[Style]]:
    """Saved quick styles by tool. Anything damaged or unknown in the setting is skipped."""
    raw = app_settings().value(SETTING, "")
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw else {}
    except ValueError:
        log.warning("Ignoring unreadable saved quick styles")
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[Style]] = {}
    for kind, entries in data.items():
        if not can_save(kind) or not isinstance(entries, list):
            continue
        styles = []
        for entry in entries[:MAX_SAVED]:
            if isinstance(entry, dict):
                try:
                    styles.append(Style.from_dict(entry))
                except (TypeError, ValueError):
                    continue
        if styles:
            out[kind] = styles
    return out


def saved_for(kind: str) -> list[Style]:
    return load().get(kind, [])


def _store(saved: dict[str, list[Style]]) -> None:
    data = {kind: [fields_of(kind, s) for s in styles] for kind, styles in saved.items() if styles}
    app_settings().setValue(SETTING, json.dumps(data))
    notifier().changed.emit()


def save(kind: str, style: Style) -> bool:
    """Add `style` to `kind`'s quick styles. False if the tool has nothing to save, it's already
    there, or the tool has MAX_SAVED already."""
    if not can_save(kind):
        return False
    saved = load()
    styles = saved.get(kind, [])
    wanted = fields_of(kind, style)
    if any(fields_of(kind, s) == wanted for s in styles) or len(styles) >= MAX_SAVED:
        return False
    _store({**saved, kind: [*styles, Style().with_changes(**wanted)]})
    return True


def delete(kind: str, index: int) -> None:
    saved = load()
    styles = saved.get(kind, [])
    if 0 <= index < len(styles):
        _store({**saved, kind: styles[:index] + styles[index + 1:]})
