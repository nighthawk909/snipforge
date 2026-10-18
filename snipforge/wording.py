"""Words that differ between Windows and macOS (docs/CROSS-PLATFORM.md): the Recycle Bin is the Trash
on a Mac, the tray is the menu bar, File Explorer is Finder, and shortcuts read ⇧⌘S, not
Ctrl+Shift+S.

Each is a function, so it follows the platform it's asked on (tests pretend to be a Mac with the
`on_a_mac` fixture). Windows wording is returned exactly as written.
"""
from __future__ import annotations

import sys

# On a Mac, Qt's Ctrl is the ⌘ Command key and its Meta is the Control key (CROSS-PLATFORM.md).
# Apple orders the symbols Control, Option, Shift, Command.
MAC_MODIFIERS = {"meta": "⌃", "alt": "⌥", "shift": "⇧", "ctrl": "⌘"}
MAC_KEYS = {"backspace": "⌫", "del": "⌦", "delete": "⌦", "enter": "Return", "return": "Return",
            "esc": "Esc", "escape": "Esc", "wheel": "scroll", "pgup": "Page Up", "pgdown": "Page Down"}
MOUSE_WORDS = ("scroll", "click", "drag")  # "⌘-click", as Apple writes it


def on_mac() -> bool:
    return sys.platform == "darwin"


def os_name() -> str:
    return "macOS" if on_mac() else "Windows"


def this_computer() -> str:
    """'this PC' or 'this Mac'."""
    return "this Mac" if on_mac() else "this PC"


def trash() -> str:
    """Where deleted snips go."""
    return "Trash" if on_mac() else "Recycle Bin"


def tray_icon() -> str:
    """SnipForge's icon by the clock (Windows) or in the menu bar (Mac)."""
    return "menu bar icon" if on_mac() else "tray icon"


def file_manager() -> str:
    return "Finder" if on_mac() else "File Explorer"


def keys(text: str) -> str:
    """A shortcut as people read it on this platform. `text` is written the Windows way ("Ctrl+Shift+S",
    "Ctrl+wheel"); on a Mac it reads "⇧⌘S" and "⌘-scroll". On Windows it's returned unchanged."""
    if not on_mac():
        return text
    parts = text.split("+")
    if text.endswith("++"):  # "Ctrl++": the key is the plus sign
        parts = [*parts[:-2], "+"]
    *modifiers, key = parts
    found = {m.strip().lower() for m in modifiers}
    if not found <= MAC_MODIFIERS.keys() or not key:
        return text  # not a shortcut ("Win+Shift+S" has no Mac meaning): leave it alone
    symbols = "".join(symbol for name, symbol in MAC_MODIFIERS.items() if name in found)
    name = MAC_KEYS.get(key.strip().lower(), key.strip())
    if name in MOUSE_WORDS:
        return f"{symbols}-{name}" if symbols else name
    return symbols + (name.upper() if len(name) == 1 else name)
