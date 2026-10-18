"""Shortcuts of your own: key combinations, set in Settings > Capture, that start a snip, repeat the
last one or open the Library from anywhere.

They're registered with Windows' RegisterHotKey, not the keyboard hook (snipforge/hotkey.py, which
only ever acts on Win+Shift+S and Print Screen). RegisterHotKey never sees any other keystroke:
Windows tells SnipForge only when one of its own combinations is pressed. A combination another app
registered first can't be taken, and the person is told so.

Windows only for now: elsewhere `GlobalShortcuts.supported` is False and Settings hides them.
"""
from __future__ import annotations

import ctypes
import logging
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Qt, Signal
from PySide6.QtGui import QKeySequence

from . import prefs

log = logging.getLogger(__name__)

SUPPORTED = sys.platform == "win32"
ACTIONS = {  # what a shortcut can do, in the order Settings lists them
    "snip": "New snip",
    "repeat": "Repeat last snip",
    "library": "Open the Library",
}
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x0008, 0x4000
FIRST_ID = 0xB000  # our hotkey ids: an application may use 0x0000-0xBFFF
_SPECIAL_KEYS = {
    Qt.Key_Space: 0x20, Qt.Key_PageUp: 0x21, Qt.Key_PageDown: 0x22, Qt.Key_End: 0x23, Qt.Key_Home: 0x24,
    Qt.Key_Left: 0x25, Qt.Key_Up: 0x26, Qt.Key_Right: 0x27, Qt.Key_Down: 0x28, Qt.Key_Print: 0x2C,
    Qt.Key_Insert: 0x2D, Qt.Key_Delete: 0x2E, Qt.Key_Pause: 0x13,
}
# Combinations that already belong to SnipForge's switches or to Windows itself.
_RESERVED = {
    "Meta+Shift+S": "Win+Shift+S is set with its own switch above",
    "Alt+Print": "Windows uses Alt+Print Screen to copy the window in front",
    "Meta+Print": "Windows uses Win+Print Screen to save a screenshot",
}
KEY_PROBLEM = ("Use a letter, a number, a function key (F1-F24), Print Screen, Space, Insert, Delete, "
               "Home, End, Page Up, Page Down, an arrow or Pause")
NEEDS_MODIFIER = "Hold Ctrl, Alt or the Windows key too, so the shortcut doesn't get in the way of typing"


def _combination(text: str):
    """The one key combination in `text` (Qt's portable text), or a reason there isn't one."""
    sequence = QKeySequence(text, QKeySequence.PortableText)
    if sequence.count() == 0:
        return KEY_PROBLEM
    if sequence.count() > 1:
        return "Press one key combination, not a sequence of them"
    return sequence[0]


def _virtual_key(key) -> int | None:
    value = key.value
    if Qt.Key_A.value <= value <= Qt.Key_Z.value or Qt.Key_0.value <= value <= Qt.Key_9.value:
        return value  # Windows' virtual keys for letters and digits are their ASCII codes
    if Qt.Key_F1.value <= value <= Qt.Key_F24.value:
        return 0x70 + value - Qt.Key_F1.value
    return _SPECIAL_KEYS.get(key)


def normalize(text: str) -> str:
    """Qt's canonical portable text for a combination ("ctrl+shift+x" -> "Ctrl+Shift+X")."""
    return QKeySequence(text, QKeySequence.PortableText).toString(QKeySequence.PortableText) if text else ""


def check(text: str) -> str | None:
    """Why `text` can't be a shortcut, or None if it can ("" means no shortcut, which is fine)."""
    if not text:
        return None
    combo = _combination(text)
    if isinstance(combo, str):
        return combo
    if _virtual_key(combo.key()) is None:
        return KEY_PROBLEM
    if not combo.keyboardModifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
        return NEEDS_MODIFIER
    reserved = _RESERVED.get(normalize(text))
    return f"{reserved}. Choose another combination" if reserved else None


def parse(text: str) -> tuple[int, int]:
    """(RegisterHotKey modifiers, virtual key) for a combination that passes `check`."""
    combo = _combination(text)
    mods = combo.keyboardModifiers()
    flags = ((MOD_CONTROL if mods & Qt.ControlModifier else 0) | (MOD_ALT if mods & Qt.AltModifier else 0)
             | (MOD_SHIFT if mods & Qt.ShiftModifier else 0) | (MOD_WIN if mods & Qt.MetaModifier else 0))
    return flags, _virtual_key(combo.key())


def label(text: str) -> str:
    """How a combination reads on Windows: Win, not Meta; Print Screen, not Print."""
    names = {"Meta": "Win", "Print": "Print Screen"}
    return "+".join(names.get(part, part) for part in text.split("+")) if text else ""


class _Win32:
    """RegisterHotKey and UnregisterHotKey, with their own argument types (hotkey.py sets others)."""

    def __init__(self) -> None:
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._register = user32.RegisterHotKey
        self._register.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self._register.restype = wintypes.BOOL
        self._unregister = user32.UnregisterHotKey
        self._unregister.argtypes = [wintypes.HWND, ctypes.c_int]
        self._unregister.restype = wintypes.BOOL

    def register(self, hwnd, hotkey_id: int, mods: int, vk: int) -> bool:
        return bool(self._register(hwnd, hotkey_id, mods, vk))

    def unregister(self, hwnd, hotkey_id: int) -> None:
        self._unregister(hwnd, hotkey_id)


class _HotkeyFilter(QAbstractNativeEventFilter):
    """Picks WM_HOTKEY out of the GUI thread's messages. The hotkeys are registered without a
    window, so Windows posts it to the thread, where only Qt's event dispatcher sees it."""

    def __init__(self, on_hotkey) -> None:
        super().__init__()
        self._on_hotkey = on_hotkey

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt API
        if bytes(event_type) in (b"windows_dispatcher_MSG", b"windows_generic_MSG"):
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and not msg.hWnd and self._on_hotkey(int(msg.wParam)):
                return True, 0
        return False, 0


class GlobalShortcuts(QObject):
    """The shortcuts of your own that are registered right now. `api` is for tests."""

    triggered = Signal(str)  # an action from ACTIONS

    def __init__(self, api=None, parent=None) -> None:
        super().__init__(parent)
        self._api = api if api is not None else (_Win32() if SUPPORTED else None)
        self._bound: dict[str, str] = {}  # action -> its combination (portable text)
        self._filter: _HotkeyFilter | None = None

    @property
    def supported(self) -> bool:
        return self._api is not None

    def bindings(self) -> dict[str, str]:
        return dict(self._bound)

    @staticmethod
    def _id(action: str) -> int:
        return FIRST_ID + list(ACTIONS).index(action)

    def set(self, action: str, text: str) -> str | None:
        """Make `text` (portable text; "" for none) the shortcut for `action`. Returns why it
        couldn't be, in words for the person, or None. A refused combination leaves the old one."""
        if action not in ACTIONS:
            raise ValueError(f"unknown shortcut action {action!r}")
        text = normalize(text)
        problem = check(text)
        if problem:
            return problem
        if not text:
            self._release(action)
            return None
        if self._api is None:
            return "Shortcuts of your own aren't available on this computer yet"
        other = next((a for a, bound in self._bound.items() if bound == text and a != action), None)
        if other is not None:
            return f"{label(text)} is already the shortcut for {ACTIONS[other]}"
        if self._bound.get(action) == text:
            return None
        old = self._bound.get(action)
        self._release(action)
        if not self._register(action, text):
            if old is not None:
                self._register(action, old)
            return f"Another app already uses {label(text)}. Choose another combination."
        return None

    def apply_saved(self) -> dict[str, str]:
        """Register the shortcuts saved in Settings. Returns {action: why it couldn't be} for any
        that couldn't (another app took the combination meanwhile)."""
        problems = {}
        for action in ACTIONS:
            text = prefs.shortcut(action)
            if text and (problem := self.set(action, text)):
                problems[action] = problem
        return problems

    def close(self) -> None:
        for action in list(self._bound):
            self._release(action)
        app = QCoreApplication.instance()
        if self._filter is not None and app is not None:
            app.removeNativeEventFilter(self._filter)
        self._filter = None

    def _register(self, action: str, text: str) -> bool:
        mods, vk = parse(text)
        if not self._api.register(None, self._id(action), mods | MOD_NOREPEAT, vk):
            log.info("Couldn't register %s for %s (taken by another app?)", label(text), action)
            return False
        self._bound[action] = text
        app = QCoreApplication.instance()
        if self._filter is None and app is not None:
            self._filter = _HotkeyFilter(self._hotkey)
            app.installNativeEventFilter(self._filter)
        return True

    def _release(self, action: str) -> None:
        if self._bound.pop(action, None) is not None and self._api is not None:
            self._api.unregister(None, self._id(action))

    def _hotkey(self, hotkey_id: int) -> bool:
        """A WM_HOTKEY arrived: run its action if it's one of ours."""
        action = next((a for a in self._bound if self._id(a) == hotkey_id), None)
        if action is None:
            return False
        self.triggered.emit(action)
        return True
