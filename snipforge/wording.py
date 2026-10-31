"""Words for Windows' own places and settings (the Recycle Bin, the tray, File Explorer), and how a
shortcut is written, kept in one place so every window says them the same way.
"""
from __future__ import annotations


def os_name() -> str:
    return "Windows"


def this_computer() -> str:
    return "this PC"


def trash() -> str:
    """Where deleted snips go."""
    return "Recycle Bin"


def trash_tip() -> str:
    """What the Library's Recycle Bin button says about deleted snips."""
    return "Snips you delete go to the Windows Recycle Bin, so you can restore them"


def tray_icon() -> str:
    """SnipForge's icon by the clock."""
    return "tray icon"


def file_manager() -> str:
    return "File Explorer"


def contrast_setting() -> str:
    """The system setting that asks apps for more contrast."""
    return "A Windows contrast theme"


def contrast_look() -> str:
    """What SnipForge does while it's on (theme/contrast.py)."""
    return "uses its colors"


def redo_keys() -> str:
    """The redo shortcut people expect (the editor also takes Ctrl+Shift+Z)."""
    return "Ctrl+Y"


def keys(text: str) -> str:
    """A shortcut as people read it ("Ctrl+Shift+S", "Ctrl+wheel"): every shortcut SnipForge shows
    goes through here, so they're all written one way."""
    return text
