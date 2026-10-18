"""Shortcuts of your own: key combinations registered with Windows' RegisterHotKey (never the
keyboard hook), set in Settings > Capture."""
import sys
import time

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent, QKeyEvent
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
    ("Ctrl+Shift+Space", (MOD_CONTROL | MOD_SHIFT, 0x20)),
    ("Ctrl+Shift+Num+5", (MOD_CONTROL | MOD_SHIFT, 0x65)),  # the number pad's 5
    ("Meta+Q", (MOD_WIN, 0x51)),
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
    ("Ctrl+Alt+Q", "AltGr"),
    ("Ctrl+Alt+Shift+2", "AltGr"),
    ("Ctrl+Meta+Shift+S", "Win+Shift+S"),
    ("Alt+F4", "closes windows"),
    ("Ctrl+F4", "closes tabs"),
    ("Ctrl+Alt+Del", "belongs to Windows"),
    ("Alt+Shift+Print", "high contrast"),
    ("Alt+Print", "Alt+Print Screen"),
    ("Ctrl+C", "Apps already use"),
    ("Alt+Space", "Apps already use"),
    ("Ctrl+Home", "Apps already use"),
    ("Ctrl+Shift+R", "SnipForge's editor rotates"),
    ("Ctrl+Shift+Z", "redo"),
    ("Ctrl+Shift+V", "paste plain text"),
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
    assert keys.set("repeat", "Ctrl+Shift+F9") is None
    (hotkey_id,) = api.registered
    assert api.registered[hotkey_id] == (MOD_CONTROL | MOD_SHIFT, 0x78)
    keys._hotkey(hotkey_id)
    keys._hotkey(99999)  # not one of ours
    assert got == ["repeat"] and keys.bindings() == {"repeat": "Ctrl+Shift+F9"}
    assert keys.set("repeat", "") is None
    assert api.registered == {} and keys.bindings() == {}
    keys.close()


def test_a_combination_another_app_owns_is_refused_and_the_old_one_kept(qapp):
    api = FakeApi(taken={(MOD_CONTROL | MOD_ALT, 0x79)})
    keys = shortcuts.GlobalShortcuts(api=api)
    assert keys.set("library", "Ctrl+Shift+L") is None
    problem = keys.set("library", "Ctrl+Alt+F10")
    assert "Another app already uses Ctrl+Alt+F10" in problem
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
    prefs.set_shortcut("library", "Ctrl+Alt+F10")
    api = FakeApi(taken={(MOD_CONTROL | MOD_ALT, 0x79)})
    keys = shortcuts.GlobalShortcuts(api=api)
    problems = keys.apply_saved()
    assert keys.bindings() == {"snip": "Ctrl+Shift+X"}
    assert list(problems) == ["library"] and "Ctrl+Alt+F10" in problems["library"]
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
    keys = shortcuts.GlobalShortcuts(api=FakeApi(taken={(MOD_CONTROL | MOD_ALT, 0x79)}))
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
    edit.changed.emit("Ctrl+Shift+F9")
    assert prefs.shortcut("repeat") == "Ctrl+Shift+F9" and settings.changed
    assert settings.shortcut_problem.isHidden()
    edit.changed.emit("")
    assert prefs.shortcut("repeat") == ""


def test_settings_explain_a_shortcut_that_cannot_be_used(settings):
    edit = settings.shortcut_edits["library"]
    edit.changed.emit("Ctrl+Shift+L")
    edit.changed.emit("Ctrl+Alt+F10")
    assert "Another app already uses Ctrl+Alt+F10" in settings.shortcut_problem.text()
    assert not settings.shortcut_problem.isHidden()
    assert prefs.shortcut("library") == "Ctrl+Shift+L" and edit.text() == "Ctrl+Shift+L"  # back to what works


def test_settings_hide_shortcuts_where_they_cannot_work(qapp, tmp_path):
    from snipforge.settings_window import SettingsWindow
    w = SettingsWindow(library_root=tmp_path)  # no shortcuts (e.g. not Windows)
    assert all(not e.isVisibleTo(w) for e in w.shortcut_edits.values())
    w.close()


def _key(widget, key, mods=Qt.NoModifier, text=""):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyPress, key, mods, text))
    QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyRelease, key, mods, text))


def _focus(widget, on: bool):
    QApplication.sendEvent(widget, QFocusEvent(QEvent.FocusIn if on else QEvent.FocusOut, Qt.MouseFocusReason))


def editor(text=""):
    from snipforge.shortcut_edit import ShortcutEdit
    edit = ShortcutEdit(text)
    edit.got = []
    edit.changed.connect(edit.got.append)
    edit.editing_log = []
    edit.editing.connect(edit.editing_log.append)
    return edit


def test_the_shortcut_editor_records_one_combination(qapp):
    edit = editor()
    _focus(edit.edit, True)
    _key(edit.edit, Qt.Key_X, Qt.ControlModifier | Qt.ShiftModifier, "X")  # finishes by itself
    assert edit.got == ["Ctrl+Shift+X"] and edit.text() == "Ctrl+Shift+X"
    _focus(edit.edit, False)
    assert edit.editing_log == [True, False] and edit.got == ["Ctrl+Shift+X"]
    edit.clear()
    assert edit.got[-1] == "" and edit.text() == ""
    edit.set_text("Meta+Alt+1")  # shown, not announced
    assert edit.got[-1] == "" and edit.text() == "Meta+Alt+1"


def test_a_lone_modifier_then_leaving_never_wipes_a_shortcut(qapp):
    edit = editor("Ctrl+Shift+F9")
    for modifier, mods in ((Qt.Key_Control, Qt.ControlModifier), (Qt.Key_Meta, Qt.MetaModifier),
                           (Qt.Key_Alt, Qt.AltModifier)):
        _focus(edit.edit, True)
        QApplication.sendEvent(edit.edit, QKeyEvent(QEvent.KeyPress, modifier, mods))
        _focus(edit.edit, False)
        edit.edit.editingFinished.emit()  # however Qt finishes it
        assert edit.got == [] and edit.text() == "Ctrl+Shift+F9", modifier
        assert not edit.edit.keySequence().isEmpty()  # the field shows it again too


def test_esc_cancels_and_backspace_clears(qapp):
    edit = editor("Ctrl+Shift+F9")
    _focus(edit.edit, True)
    _key(edit.edit, Qt.Key_Escape)
    assert edit.got == [] and edit.text() == "Ctrl+Shift+F9"
    _focus(edit.edit, True)
    _key(edit.edit, Qt.Key_Backspace)
    assert edit.got == [""] and edit.text() == ""
    _key(edit.edit, Qt.Key_Delete)  # nothing left to clear: nothing announced
    assert edit.got == [""]


def test_the_field_says_win_not_meta(qapp):
    from PySide6.QtWidgets import QLineEdit
    edit = editor("Meta+Shift+R")
    assert edit.edit.findChild(QLineEdit).text() == "Win+Shift+R"


def test_garbage_is_refused_and_keeps_the_old_shortcut(qapp):
    api = FakeApi()
    keys = shortcuts.GlobalShortcuts(api=api)
    assert keys.set("snip", "Ctrl+Shift+X") is None
    for junk in ("bogus", "Ctrl+", "Ctrl+Shift+Banana"):
        assert "a letter, a number" in keys.set("snip", junk)
    assert keys.bindings() == {"snip": "Ctrl+Shift+X"} and len(api.registered) == 1
    keys.close()


def test_shortcuts_step_aside_while_one_is_edited(qapp):
    api = FakeApi()
    keys = shortcuts.GlobalShortcuts(api=api)
    keys.set("snip", "Ctrl+Shift+X")
    keys.suspend()
    assert api.registered == {}  # pressing Ctrl+Shift+X now records it instead of snipping
    assert keys.set("repeat", "Ctrl+Shift+F9") is None  # checked free, but not taken until editing ends
    assert api.registered == {} and keys.bindings() == {"snip": "Ctrl+Shift+X", "repeat": "Ctrl+Shift+F9"}
    assert "already the shortcut for New snip" in keys.set("library", "Ctrl+Shift+X")
    assert keys.resume() == {}
    assert sorted(api.registered.values()) == [(MOD_CONTROL | MOD_SHIFT, 0x58), (MOD_CONTROL | MOD_SHIFT, 0x78)]
    keys.close()


def test_a_combination_taken_while_editing_is_reported(qapp):
    api = FakeApi()
    keys = shortcuts.GlobalShortcuts(api=api)
    keys.set("snip", "Ctrl+Shift+X")
    keys.suspend()
    api.taken.add((MOD_CONTROL | MOD_SHIFT, 0x58))  # another app grabs it meanwhile
    problems = keys.resume()
    assert list(problems) == ["snip"] and "Ctrl+Shift+X" in problems["snip"]
    assert keys.bindings() == {} and api.registered == {}
    keys.close()


def test_settings_suspend_shortcuts_while_a_field_has_the_keyboard(settings):
    keys = settings._shortcuts
    keys.set("repeat", "Ctrl+Shift+F9")
    api = keys._api
    _focus(settings.shortcut_edits["snip"].edit, True)
    assert api.registered == {}
    _focus(settings.shortcut_edits["snip"].edit, False)
    assert list(api.registered.values()) == [(MOD_CONTROL | MOD_SHIFT, 0x78)]


def test_settings_show_a_saved_shortcut_that_failed_at_start(qapp, tmp_path):
    from snipforge.settings_window import SettingsWindow
    prefs.set_shortcut("library", "Ctrl+Alt+F10")
    keys = shortcuts.GlobalShortcuts(api=FakeApi(taken={(MOD_CONTROL | MOD_ALT, 0x79)}))
    keys.apply_saved()
    w = SettingsWindow(library_root=tmp_path, shortcuts=keys)
    try:
        assert w.shortcut_edits["library"].text() == ""  # not shown as if it worked
        assert "Ctrl+Alt+F10" in w.shortcut_problem.text() and not w.shortcut_problem.isHidden()
        assert prefs.shortcut("library") == "Ctrl+Alt+F10"  # kept, so the next start tries again
    finally:
        w.close()
        keys.close()


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


def test_tab_and_shift_tab_move_on_instead_of_being_recorded(qapp, monkeypatch):
    edit = editor("Ctrl+Shift+F9")
    moves = []
    monkeypatch.setattr(edit.edit, "focusPreviousChild", lambda: moves.append("back") or True)
    _focus(edit.edit, True)
    _key(edit.edit, Qt.Key_Backtab, Qt.ShiftModifier)
    _key(edit.edit, Qt.Key_Tab, Qt.ShiftModifier)
    _key(edit.edit, Qt.Key_Tab)  # Qt's own focus chain moves on with Tab
    assert moves == ["back", "back"] and edit.got == [] and edit.text() == "Ctrl+Shift+F9"


def test_a_saved_shortcut_that_fails_can_still_be_cleared(qapp, tmp_path):
    from snipforge.settings_window import SettingsWindow
    prefs.set_shortcut("library", "Ctrl+Alt+F10")
    keys = shortcuts.GlobalShortcuts(api=FakeApi(taken={(MOD_CONTROL | MOD_ALT, 0x79)}))
    keys.apply_saved()
    w = SettingsWindow(library_root=tmp_path, shortcuts=keys)
    try:
        edit = w.shortcut_edits["library"]
        assert edit.clear_button.isEnabled()  # empty, but there's something to remove
        edit.clear_button.click()
        assert prefs.shortcut("library") == "" and w.shortcut_problem.isHidden()
        assert not edit.clear_button.isEnabled()
    finally:
        w.close()
        keys.close()
