"""Typed, validated access to the everyday preferences shown in Settings.

Theme preferences live in snipforge.theme and update choices in snipforge.update_prefs; these are
the rest. Every read falls back to a safe default when the stored value is missing or wrong.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .settings import app_settings

DEFAULT_EXPORT_DIR = Path.home() / "Pictures" / "SnipForge"
FORMATS = ("png", "jpg", "webp", "pdf")  # what Save writes (snipforge.editor.export)
CAPTURE_MODES = ("area", "window", "screen", "fixed", "scroll")  # the capture toolbar's modes, in its order
DELAYS = (0, 3, 5, 10)  # seconds the capture toolbar's Delay menu offers
DEFAULT_FIXED_SIZE = (800, 600)  # Fixed size mode's frame, in screen pixels
MIN_FIXED, MAX_FIXED = 16, 10000  # the smallest and largest side it can have
SHORTCUT_ACTIONS = ("snip", "repeat", "library", "record")  # what a shortcut of your own can do (snipforge.shortcuts)
KEYS = {
    "copy_on_capture": "capture/copy_to_clipboard",
    "hotkey_win_shift_s": "capture/hotkey_win_shift_s",
    "hotkey_print_screen": "capture/hotkey_print_screen",
    "magnifier": "capture/magnifier",
    "capture_mode": "capture/mode",
    "capture_delay": "capture/delay",
    "capture_pointer": "capture/pointer",
    "capture_fixed_size": "capture/fixed_size",
    "capture_last_area": "capture/last_area",
    "shortcut_snip": "shortcuts/snip",
    "shortcut_repeat": "shortcuts/repeat",
    "shortcut_library": "shortcuts/library",
    "shortcut_record": "shortcuts/record",
    "export_dir": "output/folder",
    "export_format": "output/format",
}


def _bool(key: str, default: bool) -> bool:
    value = app_settings().value(KEYS[key], default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):  # INI files store text
        return value.lower() == "true"
    return default


def _set(key: str, value) -> None:
    app_settings().setValue(KEYS[key], value)


def copy_on_capture() -> bool:
    return _bool("copy_on_capture", True)


def set_copy_on_capture(on: bool) -> None:
    _set("copy_on_capture", bool(on))


def hotkeys() -> tuple[bool, bool]:
    """(Win+Shift+S, Print Screen): which keys open SnipForge."""
    return _bool("hotkey_win_shift_s", True), _bool("hotkey_print_screen", True)


def set_hotkey(which: str, on: bool) -> None:
    if which not in ("win_shift_s", "print_screen"):
        raise ValueError(f"unknown shortcut {which!r}")
    _set(f"hotkey_{which}", bool(on))


def magnifier() -> bool:
    return _bool("magnifier", True)


def set_magnifier(on: bool) -> None:
    _set("magnifier", bool(on))


def capture_mode() -> str:
    """The capture toolbar's mode: the one used last, so the next snip starts there."""
    value = app_settings().value(KEYS["capture_mode"], "area")
    return value if value in CAPTURE_MODES else "area"


def set_capture_mode(mode: str) -> None:
    if mode not in CAPTURE_MODES:
        raise ValueError(f"unknown capture mode {mode!r}")
    _set("capture_mode", mode)


def capture_delay() -> int:
    """Seconds to count down before the screen freezes (0: none). Remembered, like the mode."""
    value = app_settings().value(KEYS["capture_delay"], 0)
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return 0
    return seconds if seconds in DELAYS else 0


def set_capture_delay(seconds: int) -> None:
    if seconds not in DELAYS:
        raise ValueError(f"the delay must be one of {DELAYS}, got {seconds!r}")
    _set("capture_delay", int(seconds))


def capture_pointer() -> bool:
    """Draw the mouse pointer into snips."""
    return _bool("capture_pointer", False)


def set_capture_pointer(on: bool) -> None:
    _set("capture_pointer", bool(on))


def capture_fixed_size() -> tuple[int, int]:
    """Fixed size mode's frame (width, height) in screen pixels: the last size picked."""
    value = app_settings().value(KEYS["capture_fixed_size"], "")
    try:
        width, height = (int(part) for part in str(value).split("x"))
    except ValueError:
        return DEFAULT_FIXED_SIZE
    if not (MIN_FIXED <= width <= MAX_FIXED and MIN_FIXED <= height <= MAX_FIXED):
        return DEFAULT_FIXED_SIZE
    return width, height


def set_capture_fixed_size(width: int, height: int) -> None:
    if not (MIN_FIXED <= width <= MAX_FIXED and MIN_FIXED <= height <= MAX_FIXED):
        raise ValueError(f"each side must be {MIN_FIXED} to {MAX_FIXED} pixels, got {width} x {height}")
    _set("capture_fixed_size", f"{int(width)}x{int(height)}")


@dataclass(frozen=True)
class LastArea:
    """The last snip's area, for Repeat last snip. The monitor is known by its name, `geometry`
    (where it sits and its size, in Windows' scaled pixels) and `scale`, all as they were; `rect`
    is the area in that monitor's screen pixels."""
    screen: str
    geometry: tuple[int, int, int, int]
    scale: float
    rect: tuple[int, int, int, int]

    def valid(self) -> bool:
        ints = (*self.geometry, *self.rect)
        return (isinstance(self.screen, str) and len(self.geometry) == len(self.rect) == 4
                and all(type(n) is int and abs(n) < 2**31 for n in ints)
                and self.geometry[2] > 0 and self.geometry[3] > 0 and self.rect[2] > 0 and self.rect[3] > 0
                and isinstance(self.scale, (int, float)) and 0.5 <= self.scale <= 8)


def last_area() -> LastArea | None:
    """The last snip's area, or None before the first snip (or if what's stored is unusable)."""
    try:
        value = json.loads(str(app_settings().value(KEYS["capture_last_area"], "")))
        area = LastArea(value["screen"], tuple(value["geometry"]), value["scale"], tuple(value["rect"]))
    except (ValueError, TypeError, KeyError):
        return None
    return area if area.valid() else None


def set_last_area(area: LastArea) -> None:
    if not area.valid():
        raise ValueError(f"not a usable area: {area!r}")
    _set("capture_last_area", json.dumps({"screen": area.screen, "geometry": list(area.geometry),
                                          "scale": area.scale, "rect": list(area.rect)}))


def shortcut(action: str) -> str:
    """The shortcut of your own for `action` (Qt's portable text, e.g. "Ctrl+Shift+X"), or "" for none."""
    if action not in SHORTCUT_ACTIONS:
        raise ValueError(f"unknown shortcut action {action!r}")
    value = app_settings().value(KEYS[f"shortcut_{action}"], "")
    return value if isinstance(value, str) else ""


def set_shortcut(action: str, text: str) -> None:
    if action not in SHORTCUT_ACTIONS:
        raise ValueError(f"unknown shortcut action {action!r}")
    _set(f"shortcut_{action}", str(text))


def export_dir() -> Path:
    value = app_settings().value(KEYS["export_dir"], "")
    if isinstance(value, str) and value.strip():
        path = Path(value)
        if path.is_absolute():
            return path
    return DEFAULT_EXPORT_DIR


def set_export_dir(path: Path | None) -> None:
    """None goes back to Pictures\\SnipForge."""
    if path is not None and not Path(path).is_absolute():
        raise ValueError("the save folder must be a full path")
    _set("export_dir", "" if path is None else str(path))


def export_format() -> str:
    value = app_settings().value(KEYS["export_format"], "png")
    return value if value in FORMATS else "png"


def set_export_format(fmt: str) -> None:
    if fmt not in FORMATS:
        raise ValueError(f"unsupported format {fmt!r}")
    _set("export_format", fmt)
