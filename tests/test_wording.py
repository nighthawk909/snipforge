"""Windows wording, fonts and file places: each reads and is found exactly where it always was."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from snipforge import bundle, fonts, wording


@pytest.fixture
def on_windows(monkeypatch):
    def pretend(*modules):
        for module in modules:
            monkeypatch.setattr(module, "sys", SimpleNamespace(platform="win32", frozen=False))
    return pretend


# --- words ---------------------------------------------------------------------------------
def test_windows_words_are_unchanged():
    assert (wording.trash(), wording.tray_icon(), wording.file_manager(), wording.this_computer(),
            wording.os_name()) == ("Recycle Bin", "tray icon", "File Explorer", "this PC", "Windows")
    for text in ("Ctrl+Shift+S", "Ctrl+wheel", "Ctrl+V", "Enter", "wheel"):
        assert wording.keys(text) == text


def test_redo_keys_and_the_recycle_bin_tip():
    assert wording.redo_keys() == "Ctrl+Y"
    assert "Windows Recycle Bin" in wording.trash_tip()


def test_contrast_words():
    assert wording.contrast_setting() == "A Windows contrast theme"
    assert wording.contrast_look() == "uses its colors"


# --- fonts ---------------------------------------------------------------------------------
def test_windows_keeps_segoe_ui_and_consolas(qapp):
    assert (fonts.ui_font(9).family(), fonts.ui_font(9).pointSize()) == ("Segoe UI", 9)
    assert (fonts.mono_font(8).family(), fonts.mono_font(8).pointSize()) == ("Consolas", 8)


# --- where files are -----------------------------------------------------------------------
def test_from_source_files_are_in_the_repository():
    assert bundle.resources_dir() == Path(__file__).resolve().parents[1]
    assert (bundle.resources_dir() / "THIRD_PARTY_NOTICES.md").is_file()


def test_installed_on_windows_files_are_next_to_the_exe(on_windows, monkeypatch, tmp_path):
    on_windows(bundle)
    monkeypatch.setattr(bundle.sys, "frozen", True)
    monkeypatch.setattr(bundle.sys, "executable", str(tmp_path / "SnipForge" / "SnipForge.exe"), raising=False)
    assert bundle.resources_dir() == (tmp_path / "SnipForge").resolve()


def test_caches(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert bundle.cache_dir() == tmp_path / "SnipForge" / "cache"


def test_the_theme_cache_is_in_the_cache_folder():
    from snipforge.theme import manager

    assert manager.ASSET_DIR == bundle.cache_dir() / "theme"
