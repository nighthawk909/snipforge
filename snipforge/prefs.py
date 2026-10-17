"""Typed, validated access to the everyday preferences shown in Settings.

Theme preferences live in snipforge.theme and update choices in snipforge.update_prefs; these are
the rest. Every read falls back to a safe default when the stored value is missing or wrong.
"""
from __future__ import annotations

from pathlib import Path

from .settings import app_settings

DEFAULT_EXPORT_DIR = Path.home() / "Pictures" / "SnipForge"
FORMATS = ("png", "jpg")
CAPTURE_MODES = ("area", "window", "screen")  # the capture toolbar's modes, in its order
KEYS = {
    "copy_on_capture": "capture/copy_to_clipboard",
    "hotkey_win_shift_s": "capture/hotkey_win_shift_s",
    "hotkey_print_screen": "capture/hotkey_print_screen",
    "magnifier": "capture/magnifier",
    "capture_mode": "capture/mode",
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
