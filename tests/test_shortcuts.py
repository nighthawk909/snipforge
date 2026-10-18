"""Shortcuts of your own: key combinations registered with Windows' RegisterHotKey (never the
keyboard hook), set in Settings > Capture."""
import sys
import time

import pytest
from PySide6.QtWidgets import QApplication

from snipforge import autostart, prefs, shortcuts
from snipforge.settings import app_settings
from snipforge.shortcuts import MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, MOD_WIN

NAMES = ("shortcut_snip", "shortcut_repeat", "shortcut_library")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    for name in NAMES:
        app_settings().remove(prefs.KEYS[name])
    yield
    for name in NAMES:
        app_settings().remove(prefs.KEYS[name])


class FakeApi:
    """Windows' RegisterHotKey, with some combinations already taken by "other apps"."""

    def __init__(self, taken=()):
        self.taken = set(taken)
        self.registered: dict[int, tuple[int, int]] = {}

    def register(self, _hwnd, hotkey_id, mods, vk) -> bool:
        assert mods & MOD_NOREPEAT  # holding the keys down fires once
        if (mods & ~MOD_NOREPEAT, vk) in self.taken or hotkey_id in self.registered:
            return False
        self.registered[hotkey_id] = (mods & ~MOD_NOREPEAT, vk)
        return True

    def unregister(self, _hwnd, hotkey_id) -> None:
        self.registered.pop(hotkey_id, None)


# --- what a shortcut can be --------------------------------------------------------------------

def test_prefs_and_settings_know_the_same_actions():
    assert tuple(shortcuts.ACTIONS) == prefs.SHORTCUT_ACTIONS == ("snip", "repeat", "library")


def test_shortcuts_are_remembered_and_validated():
    assert prefs.shortcut("snip") == ""
    prefs.set_shortcut("snip", "Ctrl+Shift+X")
    assert prefs.shortcut("snip") == "Ctrl+Shift+X"
    app_settings().setValue(prefs.KEYS["shortcut_repeat"], 42)
    assert prefs.shortcut("repeat") == ""
    with pytest.raises(ValueError):
        prefs.set_shortcut("launch-rockets", "Ctrl+R")


@pytest.mark.parametrize("text, expected", [
    ("Ctrl+Shift+X", (MOD_CONTROL | MOD_SHIFT, 0x58)),
    ("Alt+F9", (MOD_ALT, 0x78)),
    ("Meta+Alt+1", (MOD_WIN | MOD_ALT, 0x31)),
    ("Ctrl+Print", (MOD_CONTROL, 0x2C)),
    ("Ctrl+Alt+PgDown", (MOD_CONTROL | MOD_ALT, 0x22)),
    ("Ctrl+Alt+Shift+F24", (MOD_CONTROL | MOD_ALT | MOD_SHIFT, 0x87)),
    ("Ctrl+Space", (MOD_CONTROL, 0x20)),
])
def test_key_combinations_become_windows_hotkeys(text, expected):
    assert shortcuts.check(text) is None
    assert shortcuts.parse(text) == expected


@pytest.mark.parametrize("text, problem", [
    ("X", "Ctrl, Alt or the Windows key"),
    ("Shift+X", "Ctrl, Alt or the Windows key"),
    ("F5", "Ctrl, Alt or the Windows key"),
    ("Meta+Shift+S", "Win+Shift+S"),
    ("Ctrl+;", "a letter, a number"),
    ("Ctrl+X, Ctrl+Y", "one key combination"),
    ("Ctrl+", "a letter, a number"),
])
def test_combinations_that_would_get_in_the_way_are_refused(text, problem):
    assert problem in shortcuts.check(text)


def test_no_shortcut_is_fine():
    assert shortcuts.check("") is None


def test_labels_say_win_not_meta():
    assert shortcuts.label("Meta+Shift+R") == "Win+Shift+R"
    assert shortcuts.label("Ctrl+Alt+PgDown") == "Ctrl+Alt+PgDown"
    assert shortcuts.label("") == ""


# --- registering them --------------------------------------------------------------------------

def test_a_shortcut_registers_and_runs_its_action(qapp):
    api = FakeApi()
    keys = shortcuts.GlobalShortcuts(api=api)
    got = []
    keys.triggered.connect(got.append)
    assert keys.set("repeat", "Ctrl+Shift+R") is None
    (hotkey_id,) = api.registered
    assert api.registered[hotkey_id] == (MOD_CONTROL | MOD_SHIFT, 0x52)
    keys._hotkey(hotkey_id)
    keys._hotkey(99999)  # not one of ours
    assert got == ["repeat"] and keys.bindings() == {"repeat": "Ctrl+Shift+R"}
    assert keys.set("repeat", "") is None
    assert api.registered == {} and keys.bindings() == {}
    keys.close()


def test_a_combination_another_app_owns_is_refused_and_the_old_one_kept(qapp):
    api = FakeApi(taken={(MOD_CONTROL | MOD_ALT, 0x4C)})
    keys = shortcuts.GlobalShortcuts(api=api)
    assert keys.set("library", "Ctrl+Shift+L") is None
    problem = keys.set("library", "Ctrl+Alt+L")
    assert "Another app already uses Ctrl+Alt+L" in problem
    assert keys.bindings() == {"library": "Ctrl+Shift+L"}
    assert list(api.registered.values()) == [(MOD_CONTROL | MOD_SHIFT, 0x4C)]
    keys.close()


def test_one_combination_does_one_thing(qapp):
    keys = shortcuts.GlobalShortcuts(api=FakeApi())
    assert keys.set("snip", "Ctrl+Shift+X") is None
    assert "already the shortcut for New snip" in keys.set("repeat", "Ctrl+Shift+X")
    assert keys.set("snip", "Ctrl+Shift+X") is None  # setting the same one again is fine
    keys.close()


def test_a_refused_combination_is_never_registered(qapp):
    api = FakeApi()
    keys = shortcuts.GlobalShortcuts(api=api)
    assert "Ctrl, Alt or the Windows key" in keys.set("snip", "Shift+X")
    assert api.registered == {}
    keys.close()


def test_saved_shortcuts_are_registered_at_start_and_problems_reported(qapp):
    prefs.set_shortcut("snip", "Ctrl+Shift+X")
    prefs.set_shortcut("library", "Ctrl+Alt+L")
    api = FakeApi(taken={(MOD_CONTROL | MOD_ALT, 0x4C)})
    keys = shortcuts.GlobalShortcuts(api=api)
    problems = keys.apply_saved()
    assert keys.bindings() == {"snip": "Ctrl+Shift+X"}
    assert list(problems) == ["library"] and "Ctrl+Alt+L" in problems["library"]
    keys.close()
    assert api.registered == {}  # closing lets them all go


def test_without_windows_there_are_no_shortcuts_of_your_own(qapp, monkeypatch):
    monkeypatch.setattr(shortcuts, "SUPPORTED", False)
    keys = shortcuts.GlobalShortcuts()
    assert not keys.supported
    assert "aren't available" in keys.set("snip", "Ctrl+Shift+X")
    assert keys.set("snip", "") is None


# --- Settings > Capture --------------------------------------------------------------------------

@pytest.fixture
def settings(qapp, tmp_path):
    from snipforge.settings_window import SettingsWindow
    keys = shortcuts.GlobalShortcuts(api=FakeApi(taken={(MOD_CONTROL | MOD_ALT, 0x4C)}))
    changed = []
    w = SettingsWindow(library_root=tmp_path, shortcuts=keys, on_shortcuts_changed=lambda: changed.append(1))
    w.changed = changed
    yield w
    w.close()
    keys.close()


def test_settings_set_a_shortcut_of_your_own(settings):
    settings.show_section("capture")
    edit = settings.shortcut_edits["repeat"]
    assert edit.isVisibleTo(settings) and edit.text() == ""
    assert edit.accessibleName() == "Shortcut for Repeat last snip"
    edit.changed.emit("Ctrl+Shift+R")
    assert prefs.shortcut("repeat") == "Ctrl+Shift+R" and settings.changed
    assert settings.shortcut_problem.isHidden()
    edit.changed.emit("")
    assert prefs.shortcut("repeat") == ""


def test_settings_explain_a_shortcut_that_cannot_be_used(settings):
    prefs.set_shortcut("library", "Ctrl+Shift+L")
    settings.refresh()
    edit = settings.shortcut_edits["library"]
    edit.changed.emit("Ctrl+Alt+L")
    assert "Another app already uses Ctrl+Alt+L" in settings.shortcut_problem.text()
    assert not settings.shortcut_problem.isHidden()
    assert prefs.shortcut("library") == "Ctrl+Shift+L" and edit.text() == "Ctrl+Shift+L"  # back to what works


def test_settings_hide_shortcuts_where_they_cannot_work(qapp, tmp_path):
    from snipforge.settings_window import SettingsWindow
    w = SettingsWindow(library_root=tmp_path)  # no shortcuts (e.g. not Windows)
    assert all(not e.isVisibleTo(w) for e in w.shortcut_edits.values())
    w.close()


def test_the_shortcut_editor_records_one_combination(qapp):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    from snipforge.shortcut_edit import ShortcutEdit
    edit = ShortcutEdit("")
    got = []
    edit.changed.connect(got.append)
    target = edit.edit
    QApplication.sendEvent(target, QKeyEvent(QEvent.KeyPress, Qt.Key_X, Qt.ControlModifier | Qt.ShiftModifier, "X"))
    QApplication.sendEvent(target, QKeyEvent(QEvent.KeyRelease, Qt.Key_X, Qt.ControlModifier | Qt.ShiftModifier, "X"))
    edit.edit.editingFinished.emit()
    assert got[-1] == "Ctrl+Shift+X" and edit.text() == "Ctrl+Shift+X"
    edit.clear()
    assert got[-1] == "" and edit.text() == ""
    edit.set_text("Meta+Alt+1")  # shown, not announced
    assert got[-1] == "" and edit.text() == "Meta+Alt+1"


# --- the real thing ------------------------------------------------------------------------------

@pytest.mark.desktop
@pytest.mark.skipif(sys.platform != "win32", reason="real Windows")
def test_a_real_shortcut_fires_when_pressed(qapp):
    import ctypes
    keys = shortcuts.GlobalShortcuts()
    got = []
    keys.triggered.connect(got.append)
    combo = "Ctrl+Alt+Shift+F24"  # nothing else uses F24
    assert keys.set("library", combo) is None
    user32 = ctypes.windll.user32
    keyup = 0x0002
    vks = (0x11, 0x12, 0x10, 0x87)  # Ctrl, Alt, Shift, F24
    try:
        for vk in vks:
            user32.keybd_event(vk, 0, 0, 0)
        for vk in reversed(vks):
            user32.keybd_event(vk, 0, keyup, 0)
        deadline = time.time() + 3
        while not got and time.time() < deadline:
            QApplication.processEvents()
            time.sleep(0.02)
    finally:
        for vk in vks:
            user32.keybd_event(vk, 0, keyup, 0)
        keys.close()
    assert got == ["library"]
