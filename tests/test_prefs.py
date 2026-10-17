"""Everyday preferences: defaults, validation and the features they switch."""
from pathlib import Path

import pytest

from snipforge import hotkey, prefs
from snipforge.settings import app_settings


@pytest.fixture(autouse=True)
def clean():
    for key in prefs.KEYS.values():
        app_settings().remove(key)
    yield
    for key in prefs.KEYS.values():
        app_settings().remove(key)


def test_defaults():
    assert prefs.copy_on_capture() is True
    assert prefs.hotkeys() == (True, True)
    assert prefs.magnifier() is True
    assert prefs.export_dir() == prefs.DEFAULT_EXPORT_DIR
    assert prefs.export_format() == "png"


def test_round_trip_and_bad_values(tmp_path):
    prefs.set_copy_on_capture(False)
    prefs.set_hotkey("print_screen", False)
    prefs.set_magnifier(False)
    prefs.set_export_dir(tmp_path)
    prefs.set_export_format("jpg")
    assert (prefs.copy_on_capture(), prefs.hotkeys(), prefs.magnifier()) == (False, (True, False), False)
    assert prefs.export_dir() == tmp_path and prefs.export_format() == "jpg"
    with pytest.raises(ValueError):
        prefs.set_hotkey("ctrl_alt_del", True)
    with pytest.raises(ValueError):
        prefs.set_export_format("gif")
    with pytest.raises(ValueError):
        prefs.set_export_dir(Path("relative/folder"))
    app_settings().setValue(prefs.KEYS["export_format"], "bmp")
    app_settings().setValue(prefs.KEYS["export_dir"], "not absolute")
    app_settings().setValue(prefs.KEYS["magnifier"], "maybe")
    assert prefs.export_format() == "png" and prefs.export_dir() == prefs.DEFAULT_EXPORT_DIR
    assert prefs.magnifier() is True
    prefs.set_export_dir(None)
    assert prefs.export_dir() == prefs.DEFAULT_EXPORT_DIR


def test_turned_off_shortcuts_are_left_to_windows(monkeypatch):
    down = {hotkey.VK_LWIN, hotkey.VK_SHIFT}
    monkeypatch.setattr(hotkey, "_is_down", lambda vk: vk in down)
    assert hotkey.HotkeyListener._matches(hotkey.VK_S)
    assert not hotkey.HotkeyListener._matches(hotkey.VK_S, win_shift_s=False)
    down.clear()
    assert hotkey.HotkeyListener._matches(hotkey.VK_SNAPSHOT)
    assert not hotkey.HotkeyListener._matches(hotkey.VK_SNAPSHOT, print_screen=False)
    listener = hotkey.HotkeyListener()
    listener.set_keys(win_shift_s=True, print_screen=False)
    assert listener.keys == (True, False)
