"""The editor's tool palette: every tool, and how you've arranged the tool rail.

`TOOLS` is the full list in its default order and groups (a `None` is a divider). A `Palette` is
your arrangement: the groups in your order, which tools are hidden, your favorites (shown in a group
of their own at the top), whether the rail shows labels, and which side it's on. It's kept in the
app's settings, shared by every open editor (`notifier`), and read defensively: a tool this version
doesn't have is dropped, and a tool a stored palette doesn't know (new in this version) goes back
into its default group, so an update never loses a tool.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace

from PySide6.QtCore import QObject, Signal

from ..settings import app_settings

log = logging.getLogger(__name__)

SETTING = "editor/tool_palette"
SIDES = ("left", "right")
# name, title, key. The title is what the rail's tooltip and the Customize window say; the key is
# the tool's own key on the canvas (canvas_input.py).
TOOLS: list[tuple[str, str, str] | None] = [
    ("select", "Select and move", "V"),
    None,
    ("arrow", "Arrow", "A"), ("line", "Line", "L"), ("rect", "Rectangle", "R"), ("ellipse", "Ellipse", "E"),
    ("spotlight", "Spotlight", "S"), ("magnify", "Magnify: enlarge part of the snip", "M"),
    None,
    ("text", "Text", "T"), ("callout", "Callout: text in a bubble or box", "K"), ("stamp", "Stamp / icon", "I"),
    ("step", "Step number", "N"), ("pen", "Pen", "P"), ("highlight", "Highlighter", "H"),
    None,
    # Groups that fill whole rows of 3 keep the rail's 3-column layout short enough for a 1080p screen
    # at 200% scaling (tests/test_tool_rail.py).
    ("blur", "Blur", "B"), ("pixelate", "Pixelate", "X"), ("erase", "Smart erase: fill an area from around it", "W"),
    None,
    ("crop", "Crop", "C"), ("cutout", "Cut out: remove a strip and join the rest", "U"),
    ("move", "Smart Move: drag out part of the snip, then put it anywhere; where it was is filled in", "G"),
]
_SPECS = {spec[0]: spec for spec in TOOLS if spec is not None}
TOOL_NAMES = list(_SPECS)


def title_of(name: str) -> str:
    return _SPECS[name][1]


def key_of(name: str) -> str:
    return _SPECS[name][2]


def tooltip_of(name: str) -> str:
    """"Crop (C)": the rail's tooltip and screen-reader name."""
    return f"{title_of(name)} ({key_of(name)})"


LABELS = {"select": "Select"}  # rail labels shorter than the title


def short_title(name: str) -> str:
    """The rail's label: the title up to its colon ("Magnify", not "Magnify: enlarge part of the snip")."""
    return LABELS.get(name) or title_of(name).split(":")[0]


def _default_groups() -> list[list[str]]:
    groups: list[list[str]] = [[]]
    for spec in TOOLS:
        if spec is None:
            groups.append([])
        else:
            groups[-1].append(spec[0])
    return [g for g in groups if g]


@dataclass(frozen=True)
class Palette:
    groups: list[list[str]] = field(default_factory=_default_groups)
    favorites: list[str] = field(default_factory=list)
    hidden: frozenset[str] = frozenset()
    labels: bool = False
    side: str = "left"

    # ------------------------------------------------------------------ building
    @staticmethod
    def default() -> Palette:
        return Palette()

    @staticmethod
    def default_group_of(name: str) -> int:
        return next(i for i, group in enumerate(_default_groups()) if name in group)

    def is_default(self) -> bool:
        return self == Palette()

    def with_changes(self, **changes) -> Palette:
        if "hidden" in changes:
            changes["hidden"] = frozenset(changes["hidden"])
        return replace(self, **changes)

    # ------------------------------------------------------------------ what the rail shows
    def rail_groups(self) -> list[list[str]]:
        """The groups as the rail lays them out: favorites first, hidden tools left out, no empty group."""
        favorites = [name for name in self.favorites if name not in self.hidden]
        groups = [[name for name in group if name not in self.hidden] for group in self.groups]
        return [group for group in [favorites, *groups] if group]

    def shown(self) -> list[str]:
        """Every tool on the rail, in rail order, each once."""
        seen: list[str] = []
        for group in self.rail_groups():
            seen.extend(name for name in group if name not in seen)
        return seen

    # ------------------------------------------------------------------ arranging
    def moved(self, name: str, group: int, index: int) -> Palette:
        """`name` taken out of wherever it is and put at `index` in `group` (both clamped)."""
        groups = [[n for n in g if n != name] for g in self.groups]
        group = max(0, min(len(groups) - 1, group))
        index = max(0, min(len(groups[group]), index))
        groups[group].insert(index, name)
        return replace(self, groups=[g for g in groups if g])

    def split_group(self, group: int, index: int) -> Palette:
        """A divider inside `group` before its `index`th tool."""
        if not 0 <= group < len(self.groups) or not 0 < index < len(self.groups[group]):
            return self
        groups = [list(g) for g in self.groups]
        head, tail = groups[group][:index], groups[group][index:]
        groups[group:group + 1] = [head, tail]
        return replace(self, groups=groups)

    def join_groups(self, group: int) -> Palette:
        """The divider after `group` removed: it and the next group become one."""
        if not 0 <= group < len(self.groups) - 1:
            return self
        groups = [list(g) for g in self.groups]
        groups[group:group + 2] = [groups[group] + groups[group + 1]]
        return replace(self, groups=groups)

    def toggled_favorite(self, name: str) -> Palette:
        favorites = [n for n in self.favorites if n != name] if name in self.favorites else [*self.favorites, name]
        return replace(self, favorites=favorites)

    def toggled_hidden(self, name: str) -> Palette:
        hidden = self.hidden - {name} if name in self.hidden else self.hidden | {name}
        return replace(self, hidden=hidden)

    # ------------------------------------------------------------------ storage
    def to_dict(self) -> dict:
        return {"groups": [list(g) for g in self.groups], "favorites": list(self.favorites),
                "hidden": sorted(self.hidden), "labels": self.labels, "side": self.side}

    @staticmethod
    def from_dict(data) -> Palette:
        """Read defensively: anything that isn't a palette reads as the default, unknown tools are
        dropped, every tool appears exactly once, and tools the stored palette doesn't have (new in
        this version) go back to their default group."""
        if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
            return Palette()
        seen: set[str] = set()
        groups: list[list[str]] = []
        for group in data["groups"]:
            if not isinstance(group, list):
                continue
            kept = [n for n in group if isinstance(n, str) and n in _SPECS and n not in seen and not seen.add(n)]
            if kept:
                groups.append(kept)
        if not groups:
            return Palette()
        for name in TOOL_NAMES:
            if name not in seen:
                where = Palette.default_group_of(name)
                groups[min(where, len(groups) - 1)].append(name)
        favorites = _names(data.get("favorites"))
        hidden = frozenset(_names(data.get("hidden")))
        labels = data.get("labels") is True
        side = data["side"] if data.get("side") in SIDES else "left"
        return Palette(groups=groups, favorites=favorites, hidden=hidden, labels=labels, side=side)


def _names(value) -> list[str]:
    """The known tool names in `value`, each once, in order."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for name in value:
        if isinstance(name, str) and name in _SPECS and name not in out:
            out.append(name)
    return out


class _Notifier(QObject):
    changed = Signal()


_notifier: _Notifier | None = None


def notifier() -> _Notifier:
    """`notifier().changed` fires whenever the palette is saved, so every open editor follows."""
    global _notifier
    if _notifier is None:
        _notifier = _Notifier()
    return _notifier


def load() -> Palette:
    raw = app_settings().value(SETTING, "")
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw else None
    except (ValueError, RecursionError):
        log.warning("Ignoring an unreadable tool palette")
        return Palette()
    return Palette.from_dict(data)


def save(palette: Palette) -> None:
    app_settings().setValue(SETTING, json.dumps(palette.to_dict()))
    notifier().changed.emit()


def reset() -> None:
    save(Palette())
