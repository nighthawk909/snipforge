"""Shortcuts of your own: key combinations, set in Settings > Capture, that start a snip, repeat the
last one, open the Library, start (and stop) recording a video, or snip with a capture preset
(snipforge/presets.py), from anywhere.

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
from .presets import ACTION_PREFIX

log = logging.getLogger(__name__)

SUPPORTED = sys.platform == "win32"
ACTIONS = {  # what a shortcut can do, in the order Settings lists them
    "snip": "New snip",
    "repeat": "Repeat last snip",
    "library": "Open the Library",
    "record": "Record video, or stop recording",  # no combination until one is chosen
}
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x0008, 0x4000
FIRST_ID = 0xB000  # our hotkey ids: an application may use 0x0000-0xBFFF
FIRST_PRESET_ID = FIRST_ID + 0x100  # presets' shortcuts are numbered from here
VK_NUMPAD0 = 0x60
_NAVIGATION_KEYS = {
    Qt.Key_PageUp: 0x21, Qt.Key_PageDown: 0x22, Qt.Key_End: 0x23, Qt.Key_Home: 0x24, Qt.Key_Left: 0x25,
    Qt.Key_Up: 0x26, Qt.Key_Right: 0x27, Qt.Key_Down: 0x28, Qt.Key_Insert: 0x2D, Qt.Key_Delete: 0x2E,
}
_OTHER_KEYS = {Qt.Key_Space: 0x20, Qt.Key_Print: 0x2C, Qt.Key_Pause: 0x13}
# Combinations that belong to SnipForge's own switches or to Windows and everyday apps.
_RESERVED = {
    "Alt+Print": "Windows uses Alt+Print Screen to copy the window in front",
    "Meta+Print": "Windows uses Win+Print Screen to save a screenshot",
    "Alt+Shift+Print": "Windows uses Alt+Shift+Print Screen for high contrast",
    "Alt+F4": "Alt+F4 closes windows",
    "Ctrl+F4": "Ctrl+F4 closes tabs and documents",
    "Ctrl+Alt+Del": "Ctrl+Alt+Delete belongs to Windows",
    # Ctrl+Shift combinations most apps rely on (a shortcut of your own would take them everywhere,
    # SnipForge's own editor included).
    "Ctrl+Shift+R": "SnipForge's editor rotates with Ctrl+Shift+R, and browsers reload with it",
    "Ctrl+Shift+S": "Apps use Ctrl+Shift+S for Save as",
    "Ctrl+Shift+T": "Browsers reopen closed tabs with Ctrl+Shift+T",
    "Ctrl+Shift+Z": "Apps use Ctrl+Shift+Z to redo",
    "Ctrl+Shift+N": "Apps use Ctrl+Shift+N for a new window or folder",
    "Ctrl+Shift+V": "Apps use Ctrl+Shift+V to paste plain text",
}
KEY_PROBLEM = ("Use a letter, a number, a function key (F1-F24), Print Screen, Space, Insert, Delete, "
               "Home, End, Page Up, Page Down, an arrow or Pause.")
NEEDS_MODIFIER = "Hold Ctrl, Alt or the Windows key too, so the shortcut doesn't get in the way of typing."
APPS_USE_IT = ("Apps already use Ctrl or Alt with this key. Add Shift (for example Ctrl+Shift) or use the "
               "Windows key.")
ALTGR = ("Ctrl+Alt with a letter, number or Space types characters on many keyboards (AltGr). Use "
         "Ctrl+Shift, Alt+Shift or the Windows key instead.")
HOOK_KEY = "Win+Shift+S already starts a snip (its switch is above). Choose another combination."


def _combination(text: str):
    """The one key combination in `text` (Qt's portable text), or a reason there isn't one."""
    sequence = QKeySequence(text, QKeySequence.PortableText)
    if sequence.count() == 0 or sequence[0].key() == Qt.Key_unknown:
        return KEY_PROBLEM
    if sequence.count() > 1:
        return "Press one key combination, not a sequence of them."
    return sequence[0]


def _virtual_key(combo) -> int | None:
    key, value = combo.key(), combo.key().value
    if Qt.Key_0.value <= value <= Qt.Key_9.value:
        keypad = bool(combo.keyboardModifiers() & Qt.KeypadModifier)
        return VK_NUMPAD0 + value - Qt.Key_0.value if keypad else value
    if Qt.Key_A.value <= value <= Qt.Key_Z.value:
        return value  # Windows' virtual keys for letters are their ASCII codes
    if Qt.Key_F1.value <= value <= Qt.Key_F24.value:
        return 0x70 + value - Qt.Key_F1.value
    return _NAVIGATION_KEYS.get(key) or _OTHER_KEYS.get(key)


def _typing_key(combo) -> bool:
    """Letters, digits, Space and the arrow and page keys: what apps pair with Ctrl or Alt."""
    value = combo.key().value
    return (Qt.Key_A.value <= value <= Qt.Key_Z.value or Qt.Key_0.value <= value <= Qt.Key_9.value
            or combo.key() == Qt.Key_Space or combo.key() in _NAVIGATION_KEYS)


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
    if _virtual_key(combo) is None:
        return KEY_PROBLEM
    mods = combo.keyboardModifiers()
    ctrl, alt, shift, win = (bool(mods & m) for m in (Qt.ControlModifier, Qt.AltModifier, Qt.ShiftModifier,
                                                       Qt.MetaModifier))
    if not (ctrl or alt or win):
        return NEEDS_MODIFIER
    if win and shift and combo.key() == Qt.Key_S:
        return HOOK_KEY  # the keyboard hook takes S whenever Win and Shift are held
    reserved = _RESERVED.get(normalize(text).replace("Num+", ""))
    if reserved:
        return f"{reserved}. Choose another combination."
    if _typing_key(combo) and not win:
        is_character = combo.key() not in _NAVIGATION_KEYS
        if ctrl and alt and is_character:
            return ALTGR
        if not shift and not (ctrl and alt):
            return APPS_USE_IT
    return None


def parse(text: str) -> tuple[int, int]:
    """(RegisterHotKey modifiers, virtual key) for a combination that passes `check`."""
    combo = _combination(text)
    mods = combo.keyboardModifiers()
    flags = ((MOD_CONTROL if mods & Qt.ControlModifier else 0) | (MOD_ALT if mods & Qt.AltModifier else 0)
             | (MOD_SHIFT if mods & Qt.ShiftModifier else 0) | (MOD_WIN if mods & Qt.MetaModifier else 0))
    return flags, _virtual_key(combo)


def label(text: str) -> str:
    """How a combination reads on Windows: Win, not Meta; Print Screen, not Print."""
    names = {"Meta": "Win", "Print": "Print Screen", "Num": "Num"}
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
    """The shortcuts of your own. `api` is for tests.

    While one is being edited in Settings they're all `suspend`ed, so pressing an existing
    combination records it in the field instead of running it."""

    triggered = Signal(str)  # an action from ACTIONS, or a preset's ("preset:<id>")

    def __init__(self, api=None, parent=None) -> None:
        super().__init__(parent)
        self._api = api if api is not None else (_Win32() if SUPPORTED else None)
        self._bound: dict[str, str] = {}  # action -> its combination (portable text), known to be free
        self._live: set[str] = set()  # actions registered with Windows right now
        self._suspended = False
        self._filter: _HotkeyFilter | None = None
        self._ids: dict[str, int] = {}  # presets' hotkey ids, given out as they're first used
        self._titles: dict[str, str] = {}  # presets' names, for messages
        self.problems: dict[str, str] = {}  # saved shortcuts that couldn't be set (for Settings)

    @property
    def supported(self) -> bool:
        return self._api is not None

    def bindings(self) -> dict[str, str]:
        return dict(self._bound)

    def _id(self, action: str) -> int:
        if action in ACTIONS:
            return FIRST_ID + list(ACTIONS).index(action)
        if action not in self._ids:
            self._ids[action] = FIRST_PRESET_ID + len(self._ids)
        return self._ids[action]

    def title(self, action: str, capital: bool = False) -> str:
        """What the action is called: "New snip", or "the preset Docs" ("The preset Docs" to
        start a sentence)."""
        if action in ACTIONS:
            return ACTIONS[action]
        return f"{'The' if capital else 'the'} preset {self._titles.get(action, '')}".rstrip()

    def set(self, action: str, text: str, title: str | None = None) -> str | None:
        """Make `text` (portable text; "" for none) the shortcut for `action` (one of ACTIONS, or a
        preset's "preset:<id>", named `title`). Returns why it couldn't be, in words for the person,
        or None. A refused combination leaves the old one."""
        if action not in ACTIONS and not (action.startswith(ACTION_PREFIX) and len(action) > len(ACTION_PREFIX)):
            raise ValueError(f"unknown shortcut action {action!r}")
        if title is not None:
            self._titles[action] = title
        canonical = normalize(text)
        if text and not canonical:
            return KEY_PROBLEM
        problem = check(canonical)
        if problem:
            return problem
        self.problems.pop(action, None)
        if not canonical:
            self._unregister(action)
            self._bound.pop(action, None)
            return None
        if self._api is None:
            return "Shortcuts of your own aren't available on this computer yet."
        other = next((a for a, bound in self._bound.items() if bound == canonical and a != action), None)
        if other is not None:
            return f"{label(canonical)} is already the shortcut for {self.title(other)}."
        if self._bound.get(action) == canonical:
            return None
        old = self._bound.get(action)
        self._unregister(action)
        if not self._register(action, canonical):
            if old is not None and not self._suspended:
                self._register(action, old)
            return f"Another app already uses {label(canonical)}. Choose another combination."
        self._bound[action] = canonical
        if self._suspended:
            self._unregister(action)  # it's free; it starts working when editing ends
        return None

    def apply_saved(self) -> dict[str, str]:
        """Register the shortcuts saved in Settings. Returns {action: why it couldn't be} for any
        that couldn't (another app took the combination meanwhile); `problems` keeps them too."""
        for action in ACTIONS:
            text = prefs.shortcut(action)
            if text and (problem := self.set(action, text)):
                self.problems[action] = problem
        return dict(self.problems)

    def apply_presets(self, presets) -> dict[str, str]:
        """Give each capture preset (snipforge.presets.Preset) its shortcut, and let go of those of
        presets that are gone. Returns {action: why it couldn't be} for any that couldn't."""
        wanted = {p.action: p for p in presets}
        for action in [a for a in (*self._bound, *self.problems) if a.startswith(ACTION_PREFIX)]:
            if action not in wanted:
                self.forget(action)
        problems = {}
        for action, preset in wanted.items():
            problem = self.set(action, preset.shortcut, title=preset.name)
            if problem:
                problems[action] = self.problems[action] = problem
        return problems

    def forget(self, action: str) -> None:
        """A preset that's been deleted: its shortcut goes, and so does anything said about it."""
        self._unregister(action)
        self._bound.pop(action, None)
        self.problems.pop(action, None)
        self._titles.pop(action, None)

    def suspend(self) -> None:
        """Let go of every combination for now (while one is being edited)."""
        self._suspended = True
        for action in list(self._live):
            self._unregister(action)

    def resume(self) -> dict[str, str]:
        """Take them back. Returns {action: problem} for any another app took in the meantime."""
        self._suspended = False
        problems = {}
        for action, text in list(self._bound.items()):
            if action not in self._live and not self._register(action, text):
                del self._bound[action]
                problems[action] = f"Another app took {label(text)} while you were editing. Choose another."
        self.problems.update(problems)
        return problems

    def close(self) -> None:
        for action in list(self._live):
            self._unregister(action)
        self._bound.clear()
        app = QCoreApplication.instance()
        if self._filter is not None and app is not None:
            app.removeNativeEventFilter(self._filter)
        self._filter = None

    def _register(self, action: str, text: str) -> bool:
        mods, vk = parse(text)
        if not self._api.register(None, self._id(action), mods | MOD_NOREPEAT, vk):
            log.info("Couldn't register %s for %s (taken by another app?)", label(text), action)
            return False
        self._live.add(action)
        app = QCoreApplication.instance()
        if self._filter is None and app is not None:
            self._filter = _HotkeyFilter(self._hotkey)
            app.installNativeEventFilter(self._filter)
        return True

    def _unregister(self, action: str) -> None:
        if action in self._live:
            self._live.discard(action)
            self._api.unregister(None, self._id(action))

    def _hotkey(self, hotkey_id: int) -> bool:
        """A WM_HOTKEY arrived: run its action if it's one of ours."""
        action = next((a for a in self._live if self._id(a) == hotkey_id), None)
        if action is None:
            return False
        self.triggered.emit(action)
        return True
