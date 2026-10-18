"""Mac wording, fonts and file places (docs/MAC-PLAN.md phase 1): each platform reads and finds its
own, and Windows keeps exactly what it had."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QFont, QFontDatabase, QKeySequence

from snipforge import bundle, fonts, wording
from snipforge.theme import packs


@pytest.fixture
def on_windows(monkeypatch):
    def pretend(*modules):
        for module in modules:
            monkeypatch.setattr(module, "sys", SimpleNamespace(platform="win32", frozen=False))
    return pretend


# --- words ---------------------------------------------------------------------------------
def test_windows_words_are_unchanged(on_windows):
    on_windows(wording)
    assert (wording.trash(), wording.tray_icon(), wording.file_manager(), wording.this_computer(),
            wording.os_name()) == ("Recycle Bin", "tray icon", "File Explorer", "this PC", "Windows")
    for text in ("Ctrl+Shift+S", "Ctrl+wheel", "Ctrl+V", "Enter", "wheel"):
        assert wording.keys(text) == text


def test_mac_words(on_a_mac):
    on_a_mac(wording)
    assert (wording.trash(), wording.tray_icon(), wording.file_manager(), wording.this_computer(),
            wording.os_name()) == ("Trash", "menu bar icon", "Finder", "this Mac", "macOS")


@pytest.mark.parametrize(("windows", "mac"), [
    ("Ctrl+V", "⌘V"),
    ("Ctrl+Shift+S", "⇧⌘S"),  # Apple's order: Control, Option, Shift, Command
    ("Shift+Ctrl+S", "⇧⌘S"),
    ("Ctrl+Alt+Shift+Meta+x", "⌃⌥⇧⌘X"),  # Qt's Meta is the Control key on a Mac
    ("Ctrl+0", "⌘0"),
    ("Ctrl+=", "⌘="),
    ("Ctrl+-", "⌘-"),
    ("Ctrl++", "⌘+"),
    ("Ctrl+F4", "⌘F4"),
    ("Enter", "Return"),
    ("Ctrl+Backspace", "⌘⌫"),  # Finder's Move to Trash
    ("Del", "⌦"),
    ("Ctrl+wheel", "⌘-scroll"),
    ("Ctrl+click", "⌘-click"),
    ("wheel", "scroll"),
    ("Win+Shift+S", "Win+Shift+S"),  # no Mac meaning: left alone, never half-translated
])
def test_mac_shortcuts_read_the_mac_way(on_a_mac, windows, mac):
    on_a_mac(wording)
    assert wording.keys(windows) == mac


@pytest.mark.mac
@pytest.mark.parametrize("shortcut", ["Ctrl+V", "Ctrl+Shift+S", "Ctrl+Alt+Shift+Meta+X", "Ctrl+0", "Ctrl+F4",
                                      "Ctrl+Backspace"])
def test_mac_shortcuts_match_what_macos_menus_show(qapp, shortcut):
    """Qt's own menus on a real Mac show shortcuts the way keys() writes them."""
    assert wording.keys(shortcut) == QKeySequence(shortcut).toString(QKeySequence.NativeText)


# --- fonts ---------------------------------------------------------------------------------
def test_windows_keeps_segoe_ui_and_consolas(on_windows, qapp):
    on_windows(fonts)
    assert (fonts.ui_font(9).family(), fonts.ui_font(9).pointSize()) == ("Segoe UI", 9)
    assert (fonts.mono_font(8).family(), fonts.mono_font(8).pointSize()) == ("Consolas", 8)


def test_a_mac_gets_its_system_font_and_menlo(on_a_mac, qapp):
    on_a_mac(fonts)
    ui, mono = fonts.ui_font(11), fonts.mono_font(10)
    assert ui.family() == QFontDatabase.systemFont(QFontDatabase.GeneralFont).family() != "Segoe UI"
    assert ui.pointSize() == 11
    assert (mono.family(), mono.pointSize(), mono.styleHint()) == ("Menlo", 10, QFont.Monospace)


# --- where files are -----------------------------------------------------------------------
def test_from_source_files_are_in_the_repository():
    assert bundle.resources_dir() == Path(__file__).resolve().parents[1]
    assert (bundle.resources_dir() / "THIRD_PARTY_NOTICES.md").is_file()


def test_installed_on_windows_files_are_next_to_the_exe(on_windows, monkeypatch, tmp_path):
    on_windows(bundle)
    monkeypatch.setattr(bundle.sys, "frozen", True)
    monkeypatch.setattr(bundle.sys, "executable", str(tmp_path / "SnipForge" / "SnipForge.exe"), raising=False)
    assert bundle.resources_dir() == (tmp_path / "SnipForge").resolve()


def test_in_the_mac_app_files_are_in_contents_resources(on_a_mac, monkeypatch, tmp_path):
    on_a_mac(bundle, packs)
    app = tmp_path / "SnipForge.app" / "Contents"
    for module in (bundle, packs):
        monkeypatch.setattr(module.sys, "frozen", True, raising=False)
        monkeypatch.setattr(module.sys, "executable", str(app / "MacOS" / "SnipForge"), raising=False)
    assert bundle.resources_dir() == (app / "Resources").resolve()
    assert packs.pack_folders()[0] == (app / "Resources" / "themes").resolve()  # an organization's packs


def test_caches(on_a_mac, on_windows, monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    on_windows(bundle)
    assert bundle.cache_dir() == tmp_path / "SnipForge" / "cache"
    on_a_mac(bundle)
    assert bundle.cache_dir() == Path.home() / "Library" / "Caches" / "SnipForge"


def test_the_theme_cache_is_in_the_cache_folder():
    from snipforge.theme import manager

    assert manager.ASSET_DIR == bundle.cache_dir() / "theme"


def test_redo_and_trash_shortcuts(on_windows, on_a_mac):
    on_windows(wording)
    assert (wording.redo_keys(), wording.trash_shortcut()) == ("Ctrl+Y", None)
    assert "Windows Recycle Bin" in wording.trash_tip()
    on_a_mac(wording)
    assert (wording.redo_keys(), wording.trash_shortcut()) == ("⇧⌘Z", "Ctrl+Backspace")
    assert "Trash" in wording.trash_tip() and "Windows" not in wording.trash_tip()


@pytest.mark.mac
def test_the_editor_reads_the_mac_way_on_a_mac(qapp):
    """Its hints and tooltips are written when it loads, so only a real Mac shows them (the CI job)."""
    from PySide6.QtWidgets import QAbstractButton

    from snipforge.editor import chrome, panel

    assert "⌘D" in panel.HINTS["select"] and "⌘-click" in panel.HINTS["select"]
    assert [tip for _action, _icon, tip in panel.IMAGE_ACTIONS[:2]] == ["Rotate left (⇧⌘R)", "Rotate right (⌘R)"]
    bar = chrome.CommandBar()
    tips = " ".join(button.toolTip() for button in bar.findChildren(QAbstractButton))
    assert "⌘Z" in tips and "⇧⌘Z" in tips and "⌘C" in tips and "Ctrl" not in tips
