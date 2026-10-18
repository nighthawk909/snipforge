"""Capture presets: saved combinations of how to snip and what happens to the snip afterwards.

A preset holds a capture mode, a delay, whether the pointer is drawn in, Fixed size mode's size,
effects (Beautify, a border, torn edges) and where the snip goes: the editor, the clipboard, or a
folder. Each can have a shortcut of its own, registered with RegisterHotKey like the other
shortcuts of your own (snipforge/shortcuts.py), never the keyboard hook. They're chosen from the
capture toolbar, the tray menu and Settings > Capture, and kept as JSON in the settings.

Choosing a preset never changes your usual choices (the mode, delay and pointer the next ordinary
snip starts with).
"""
from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass, field, replace

from PySide6.QtCore import QObject, Signal

from .prefs import CAPTURE_MODES, DEFAULT_FIXED_SIZE, DELAYS, MAX_FIXED, MIN_FIXED
from .settings import app_settings

log = logging.getLogger(__name__)

KEY = "capture/presets"
ACTION_PREFIX = "preset:"  # a preset's shortcut action in snipforge.shortcuts
MAX_PRESETS = 20
MAX_NAME = 40
DESTINATIONS = {"editor": "Open the editor", "copy": "Copy to the clipboard", "save": "Save to a folder"}
BEAUTIFY = {"settings": "As in Settings", "on": "On", "off": "Off"}
BORDERS = {0: "None", 3: "Thin", 8: "Thick"}
SIDES = ("top", "right", "bottom", "left")
TORN = {"": "None", "bottom": "Bottom", "right,bottom": "Right and bottom", "all": "All sides"}
MODE_NAMES = {"area": "Area", "window": "Window", "screen": "Full screen", "fixed": "Fixed size",
              "scroll": "Scrolling"}


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    mode: str = "area"
    delay: int = 0
    pointer: bool = False
    fixed_size: tuple[int, int] = DEFAULT_FIXED_SIZE
    beautify: str = "settings"  # a key of BEAUTIFY
    border: int = 0  # pixels; a key of BORDERS
    torn: frozenset[str] = field(default_factory=frozenset)  # which sides are torn
    destination: str = "editor"  # a key of DESTINATIONS
    folder: str = ""  # for "save": a full path, or "" for the Save folder in Settings
    shortcut: str = ""  # Qt's portable text, or "" for none

    @property
    def action(self) -> str:
        """Its shortcut's action name in snipforge.shortcuts."""
        return ACTION_PREFIX + self.id

    @property
    def torn_key(self) -> str:
        """Its torn sides as a key of TORN ("" for none)."""
        if self.torn == frozenset(SIDES):
            return "all"
        return ",".join(side for side in SIDES if side in self.torn)

    @property
    def has_edges(self) -> bool:
        return bool(self.border or self.torn)

    def beautify_on(self, setting: bool) -> bool:
        """Whether Beautify frames this preset's snips, given the Beautify switch in Settings."""
        return {"on": True, "off": False}.get(self.beautify, setting)

    def summary(self) -> str:
        """For example "Area · 3 s delay · pointer · Beautify, border · copy"."""
        mode = MODE_NAMES.get(self.mode, self.mode)
        if self.mode == "fixed":
            mode += f" {self.fixed_size[0]} × {self.fixed_size[1]}"
        parts = [mode]
        if self.delay:
            parts.append(f"{self.delay} s delay")
        if self.pointer:
            parts.append("pointer")
        effects = [name for name, on in (("Beautify", self.beautify == "on"), ("no Beautify", self.beautify == "off"),
                                         ("border", bool(self.border)), ("torn edges", bool(self.torn))) if on]
        if effects:
            parts.append(", ".join(effects))
        parts.append({"editor": "editor", "copy": "copy", "save": "save"}[self.destination])
        return " · ".join(parts)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "mode": self.mode, "delay": self.delay, "pointer": self.pointer,
                "fixed_size": list(self.fixed_size), "beautify": self.beautify, "border": self.border,
                "torn": [side for side in SIDES if side in self.torn], "destination": self.destination,
                "folder": self.folder, "shortcut": self.shortcut}

    @staticmethod
    def from_dict(data) -> Preset | None:
        """A preset from saved settings, or None if it isn't one. Unusable details fall back to
        their defaults; an unusable id or name drops the preset."""
        if not isinstance(data, dict):
            return None
        pid, name = data.get("id"), data.get("name")
        if not (isinstance(pid, str) and pid.isalnum() and len(pid) <= 16):
            return None
        if not (isinstance(name, str) and name.strip()):
            return None
        defaults = Preset(pid, clean_name(name))
        size = data.get("fixed_size")
        fixed = tuple(size) if (isinstance(size, list) and len(size) == 2
                                and all(type(n) is int and MIN_FIXED <= n <= MAX_FIXED for n in size)) \
            else DEFAULT_FIXED_SIZE
        torn = data.get("torn")
        folder = data.get("folder")
        shortcut = data.get("shortcut")
        return replace(
            defaults,
            mode=_one_of(data.get("mode"), CAPTURE_MODES, defaults.mode),
            delay=_one_of(data.get("delay"), DELAYS, defaults.delay),
            pointer=data.get("pointer") is True,
            fixed_size=fixed,
            beautify=_one_of(data.get("beautify"), BEAUTIFY, defaults.beautify),
            border=_one_of(data.get("border"), BORDERS, defaults.border),
            torn=frozenset(s for s in torn if s in SIDES) if isinstance(torn, list) else frozenset(),
            destination=_one_of(data.get("destination"), DESTINATIONS, defaults.destination),
            folder=folder if isinstance(folder, str) else "",
            shortcut=shortcut if isinstance(shortcut, str) else "")


def _one_of(value, choices, default):
    return value if type(value) in (str, int) and value in choices else default


def clean_name(name: str) -> str:
    return " ".join(name.split())[:MAX_NAME]


def torn_sides(key: str) -> frozenset[str]:
    """The sides a key of TORN tears."""
    if key == "all":
        return frozenset(SIDES)
    return frozenset(side for side in key.split(",") if side in SIDES)


def new_id(existing: list[Preset]) -> str:
    taken = {p.id for p in existing}
    while (pid := secrets.token_hex(4)) in taken:
        pass
    return pid


class _Changes(QObject):
    changed = Signal()


_changes: _Changes | None = None


def changes() -> _Changes:
    """`changes().changed` fires after the presets are saved (the tray menu and toolbar follow)."""
    global _changes
    if _changes is None:
        _changes = _Changes()
    return _changes


def load() -> list[Preset]:
    raw = app_settings().value(KEY, "")
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw else []
    except ValueError:
        log.warning("The saved capture presets couldn't be read; starting without them")
        return []
    presets, seen = [], set()
    for item in data if isinstance(data, list) else []:
        preset = Preset.from_dict(item)
        if preset is not None and preset.id not in seen:
            seen.add(preset.id)
            presets.append(preset)
    return presets[:MAX_PRESETS]


def save(presets: list[Preset]) -> None:
    if len(presets) > MAX_PRESETS:
        raise ValueError(f"at most {MAX_PRESETS} presets")
    app_settings().setValue(KEY, json.dumps([p.to_dict() for p in presets]))
    changes().changed.emit()


def get(preset_id: str) -> Preset | None:
    return next((p for p in load() if p.id == preset_id), None)


def from_action(action: str) -> str | None:
    """The preset id in a shortcut action ("preset:<id>"), or None if it isn't a preset's."""
    return action[len(ACTION_PREFIX):] if action.startswith(ACTION_PREFIX) else None
