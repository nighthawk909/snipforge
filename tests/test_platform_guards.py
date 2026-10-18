"""Windows-only features stand down cleanly on other platforms (the Mac build, docs/MAC-PLAN.md).

These pretend to be a Mac, so they run on Windows too and the Windows CI keeps the guards honest.
"""
from pathlib import Path

import pytest
from PySide6.QtGui import QDesktopServices, QImage, QShortcut
from PySide6.QtWidgets import QSystemTrayIcon

from snipforge import app, autostart, home, hotkey, ocr, settings_window, share, winrects, wording
from snipforge.library import Library


@pytest.fixture
def mac(monkeypatch, on_a_mac):
    on_a_mac(app, home, ocr, share, wording)
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


# --- Mac wording (docs/MAC-PLAN.md phase 1) --------------------------------------------------
def test_the_library_speaks_of_the_trash_and_the_menu_bar(mac, qapp, tmp_path, monkeypatch):
    library = Library(tmp_path / "lib")
    window = home.HomeWindow(library, lambda: None, lambda _entry_id: None)
    try:
        window.refresh()
        assert window._bin.text() == "Trash" and "Trash" in window._bin.toolTip()
        assert "Windows" not in window._bin.toolTip()
        assert "menu bar icon" in window._empty_hint.text() and "Win+" not in window._empty_hint.text()
        many = [library.create(QImage(8, 8, QImage.Format_ARGB32)).id for _ in range(home.CONFIRM_TRASH_OVER + 1)]
        assert window._trash_question(many) == f"Move {len(many)} snips to the Trash?"
        trashed = []
        monkeypatch.setattr(window, "_trash", trashed.append)
        keys = {s.key().toString(): s for s in window.grid.findChildren(QShortcut)}
        keys["Ctrl+Backspace"].activated.emit()  # ⌘⌫, as in Finder
        assert len(trashed) == 1
    finally:
        window.close()


def test_settings_on_a_mac_show_no_windows_keys(mac, qapp, tmp_path):
    window = settings_window.SettingsWindow(library_root=tmp_path / "lib")

    def shown(section: str) -> str:
        page = window._pages[section]
        return " ".join(label.text() for label in page.findChildren(settings_window.QLabel) if label.isVisibleTo(page))

    try:
        capture = window._pages["capture"]
        assert not window.key_win_shift_s.isVisibleTo(capture) and not window.key_print_screen.isVisibleTo(capture)
        assert "menu bar icon" in shown("capture") and "Win+Shift+S" not in shown("capture")
        assert "Win+Shift+S" not in shown("general")
        assert "this Mac" in shown("about") and "this PC" not in shown("about")
    finally:
        window.close()
    assert settings_window.theme_choices()[0] == ("system", "Match macOS")


def test_settings_open_folder_says_so_when_finder_cant(mac, qapp, tmp_path, monkeypatch, no_dialogs):
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: False)
    window = settings_window.SettingsWindow(library_root=tmp_path / "lib")
    try:
        window._open_folder(tmp_path / "lib")
    finally:
        window.close()
    assert [kind for kind, _ in no_dialogs] == ["warning"] and "Finder" in no_dialogs[0][1][2]


def test_about_on_a_mac(mac):
    text = app.about_text()
    assert "for Mac" in text and "Contents/Resources" in text and "this Mac" in text
    assert "Windows" not in text and "this PC" not in text


def test_the_share_menu_offers_no_windows_share_sheet(mac, qapp):
    from PySide6.QtWidgets import QMenu, QWidget

    from snipforge.editor.share_menu import ShareController

    owner = QWidget()
    controller = ShareController(owner)
    menu = QMenu()
    controller._populate(menu)
    texts = [action.text() for action in menu.actions() if not action.isSeparator()]
    assert texts[0].startswith("Email\t") and not any("Outlook" in text for text in texts)
    assert "Save and show in folder" in texts
    owner.deleteLater()


def test_windows_keeps_its_key_switches_and_words(qapp, tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(hotkey, "SUPPORTED", True)
    monkeypatch.setattr(wording, "sys", SimpleNamespace(platform="win32"))
    window = settings_window.SettingsWindow(library_root=tmp_path / "lib")
    try:
        capture = window._pages["capture"]
        assert window.key_win_shift_s.isVisibleTo(capture) and window.key_print_screen.isVisibleTo(capture)
        assert window._keys_note().startswith("A shortcut you turn off goes back to Windows")
    finally:
        window.close()
    assert settings_window.theme_choices()[0] == ("system", "Match Windows")
    assert "for Windows" in app.about_text() and "in the install folder" in app.about_text()
    assert home.snip_hint() == "Press Win+Shift+S or Print Screen to snip from anywhere."


def test_library_open_folder_says_so_when_finder_cant(mac, qapp, tmp_path, monkeypatch, no_dialogs):
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: False)
    window = home.HomeWindow(Library(tmp_path / "lib"), lambda: None, lambda _entry_id: None)
    try:
        window._reveal(tmp_path / "lib")
    finally:
        window.close()
    assert [kind for kind, _ in no_dialogs] == ["warning"] and "Finder" in no_dialogs[0][1][2]
