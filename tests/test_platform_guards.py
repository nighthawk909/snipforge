"""Windows-only features stand down cleanly on other platforms (the Mac build, docs/MAC-PLAN.md).

These pretend to be a Mac, so they run on Windows too and the Windows CI keeps the guards honest.
"""
from pathlib import Path

import pytest
from PySide6.QtGui import QDesktopServices, QImage
from PySide6.QtWidgets import QSystemTrayIcon

from snipforge import app, autostart, home, hotkey, ocr, settings_window, share, winrects
from snipforge.library import Library


@pytest.fixture
def mac(monkeypatch, on_a_mac):
    on_a_mac(app, home, ocr, settings_window, share)
    for module in (autostart, hotkey, winrects):
        monkeypatch.setattr(module, "SUPPORTED", False)


def test_no_keyboard_hook_but_the_shortcut_settings_still_work(mac, qapp):
    listener = hotkey.HotkeyListener()
    assert listener.start() is False
    listener.refresh()  # nothing installed, nothing to do
    listener.stop()
    listener.set_keys(win_shift_s=True, print_screen=False)
    assert listener.keys == (True, False)


def test_no_window_list(mac):
    assert winrects.window_infos() == [] and winrects.visible_windows() == []
    assert winrects.friendly_app_name("C:/Program Files/Microsoft Office/root/Office16/EXCEL.EXE") == "Excel"


def test_start_at_sign_in_reads_off_and_explains_why_it_cant_turn_on(mac):
    assert autostart.is_enabled() is False and autostart.enabled_for_all_users() is False
    with pytest.raises(OSError, match="isn't available"):
        autostart.set_enabled(True)


def test_ocr_says_it_is_unavailable(mac, qapp):
    image = QImage(40, 20, QImage.Format_ARGB32)
    with pytest.raises(ocr.OcrUnavailable, match="Windows"):
        ocr.recognize(image)


def test_share_offers_no_windows_commands_but_still_exports(mac, tmp_path, white_image):
    assert share.onedrive_accounts() == []
    path = share.export(white_image, tmp_path)
    assert path.exists()
    assert share.invoke_shell_verb(path, share.WINDOWS_SHARE_VERB) is False
    with pytest.raises(share.ShareError, match="copied instead") as error:  # the editor copies and opens mail
        share.email_with_outlook(path, "Snip", 100)
    assert "pywin32" not in str(error.value)


def test_library_folder_and_trash_open_in_finder(mac, qapp, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(Path(url.toLocalFile())) or True)
    window = home.HomeWindow(Library(tmp_path / "lib"), lambda: None, lambda _entry_id: None)
    try:
        window._reveal(tmp_path / "lib")
        window._open_recycle_bin()
    finally:
        window.close()
    assert opened == [tmp_path / "lib", Path.home() / ".Trash"]


def test_settings_open_folder_opens_finder(mac, qapp, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(Path(url.toLocalFile())) or True)
    window = settings_window.SettingsWindow(library_root=tmp_path / "lib")
    try:
        window._open_folder(tmp_path / "lib")
    finally:
        window.close()
    assert opened == [tmp_path / "lib"]


def test_menu_bar_icon_opens_its_menu_without_snipping_or_mentioning_windows_keys(mac, qapp, tmp_path, monkeypatch,
                                                                                    no_dialogs):
    monkeypatch.setattr(app, "HOTKEYS_SUPPORTED", False)
    monkeypatch.setattr(app.UpdateFlow, "start", lambda self: None)  # no update checks in unit tests
    messages, snips = [], []
    monkeypatch.setattr(QSystemTrayIcon, "showMessage", lambda self, *a: messages.append(a))
    monkeypatch.setattr(app.SnipForgeApp, "capture", lambda self: snips.append(True))
    forge = app.SnipForgeApp(qapp, Library(tmp_path))
    try:
        forge._tray.activated.emit(QSystemTrayIcon.Trigger)  # a click: macOS opens the menu
        assert snips == [] and messages == []  # no snip, and no "couldn't install the hook" warning
        assert "Win+Shift+S" not in forge._tray.toolTip() and "click this icon, then New snip" in forge._tray.toolTip()
    finally:
        forge._tray.hide()
        forge.indexer.close()
