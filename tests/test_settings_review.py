"""Regression tests for the Settings code review: real wiring, the real update engine, the real hook."""
import ctypes
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtWidgets import QApplication

from snipforge import autostart, hotkey, prefs
from snipforge.library import Library
from snipforge.settings import app_settings
from snipforge.update_controller import UpdateController
from snipforge.update_policy import PolicyInfo
from snipforge.update_prefs import UpdatePrefs
from snipforge.updates_page import UpdatesPage
from tests.test_update_controller import FakeSource, rel


@pytest.fixture(autouse=True)
def clean_prefs():
    keys = list(prefs.KEYS.values())
    for key in keys:
        app_settings().remove(key)
    yield
    for key in keys:
        app_settings().remove(key)


@pytest.fixture
def forge(qapp, tmp_path, monkeypatch):
    from snipforge import app as app_module
    monkeypatch.setattr(app_module.HotkeyListener, "start", lambda self: True)
    monkeypatch.setattr(app_module.HotkeyListener, "stop", lambda self: None)
    monkeypatch.setattr(app_module.UpdateFlow, "start", lambda self: None)
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    f = app_module.SnipForgeApp(qapp, Library(tmp_path))
    f.indexer._recognize = lambda image: ""
    yield f
    for window in (f._home, f._settings, *f._windows):
        if window is not None:
            window.close()
    f.indexer.close()


def test_library_sidebar_opens_settings(forge):
    forge.show_home()
    forge._home._settings.click()
    assert forge._settings is not None and forge._settings.isVisible()
    assert forge._settings.current_section() == "general"
    forge.show_settings("nonsense")  # a bad section falls back instead of failing
    assert forge._settings.current_section() == "general"


def test_tray_prompts_are_only_quiet_while_settings_is_in_front(forge, monkeypatch):
    forge.show_settings("updates")
    settings = forge._settings
    monkeypatch.setattr(QGuiApplication, "applicationState", staticmethod(lambda: Qt.ApplicationActive))
    monkeypatch.setattr(settings, "isActiveWindow", lambda: True)
    assert forge._settings_shows_updates()
    monkeypatch.setattr(settings, "isActiveWindow", lambda: False)  # behind another window
    assert not forge._settings_shows_updates()
    monkeypatch.setattr(settings, "isActiveWindow", lambda: True)
    settings.show_section("general")
    assert not forge._settings_shows_updates()


def test_install_buttons_work_after_a_real_check(qapp, tmp_path):
    source = FakeSource([rel("0.6.0"), rel("0.5.0"), rel("0.4.0")])
    controller = UpdateController(policy=PolicyInfo(), source=source, download_dir=tmp_path / "dl",
                                  current_version="0.5.0", install_mode="per-user",
                                  launcher=lambda path, relaunch: "", quit_app=lambda: None)
    controller.set_prefs(UpdatePrefs(mode="manual"))
    page = UpdatesPage(controller)
    controller.check_now()
    deadline = time.time() + 5
    while controller.busy and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    QApplication.processEvents()
    assert controller.state.kind == "available"
    buttons = {page._versions.itemAt(n).widget().title.text(): page._versions.itemAt(n).widget().control
               for n in range(page._versions.count())}
    assert buttons["0.6.0"].isEnabled() and buttons["0.4.0"].isEnabled() and buttons["0.5.0"] is None
    assert page._message.text().count("0.6.0") <= 1


@pytest.mark.windows
def test_turned_off_shortcuts_pass_through_the_real_hook_callback(monkeypatch):
    class FakeUser32:
        def __init__(self):
            self.injected = []

        def GetAsyncKeyState(self, vk):  # noqa: N802 - mirrors user32
            return 0x8000 if vk in (hotkey.VK_LWIN, hotkey.VK_SHIFT) else 0

        def CallNextHookEx(self, *_a):  # noqa: N802
            return 42

        def keybd_event(self, *a):
            self.injected.append(a)

    fake = FakeUser32()
    monkeypatch.setattr(hotkey, "_user32", fake)
    listener = hotkey.HotkeyListener()
    fired = []
    listener.triggered.connect(lambda: fired.append(True))
    info = hotkey.KBDLLHOOKSTRUCT(vkCode=hotkey.VK_S, scanCode=0, flags=0, time=0, dwExtraInfo=0)
    listener.set_keys(win_shift_s=False, print_screen=True)
    assert listener._callback(0, hotkey.WM_KEYDOWN, ctypes.addressof(info)) == 42  # Windows gets it
    assert fired == [] and fake.injected == []
    listener.set_keys(win_shift_s=True, print_screen=True)
    assert listener._callback(0, hotkey.WM_KEYDOWN, ctypes.addressof(info)) == 1  # SnipForge takes it
    assert fired == [True]


def test_startup_switch_shows_what_really_happened(qapp, monkeypatch, tmp_path, _never_block_on_dialogs):
    from snipforge.settings_window import SettingsWindow

    def refused(_on):
        raise OSError("access denied")

    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    monkeypatch.setattr(autostart, "set_enabled", refused)
    monkeypatch.setattr(autostart, "enabled_for_all_users", lambda: False)
    w = SettingsWindow(library_root=tmp_path)
    w.startup.click()
    assert not w.startup.isChecked() and "warning" in _never_block_on_dialogs
    w.close()


def test_settings_reread_changes_made_elsewhere(qapp, monkeypatch, tmp_path):
    from snipforge.settings_window import SettingsWindow
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    w = SettingsWindow(library_root=tmp_path)
    w.show()
    w.hide()
    prefs.set_hotkey("print_screen", False)
    monkeypatch.setattr(autostart, "is_enabled", lambda: True)
    w.show()
    assert not w.key_print_screen.isChecked() and w.startup.isChecked()
    w.close()


def test_a_failed_save_tries_the_save_folder_again(qapp, tmp_path, white_image, _never_block_on_dialogs):
    from snipforge.editor.window import EditorWindow
    blocked = tmp_path / "file-not-folder"
    blocked.write_text("x", encoding="utf-8")
    prefs.set_export_dir(blocked)
    lib = Library(tmp_path / "lib")
    w = EditorWindow(white_image, lib, lib.create(white_image))
    w.save()
    assert "warning" in _never_block_on_dialogs and w.export_path is None
    prefs.set_export_dir(tmp_path / "Good")
    prefs.set_export_format("jpg")
    w.save()
    assert w.export_path.parent == tmp_path / "Good" and w.export_path.exists()
    assert not QImage(str(w.export_path)).isNull()
    w.close()


_ = QColor  # imported for readers adding image checks


@pytest.mark.windows
def test_holding_a_snip_key_is_one_snip(monkeypatch):
    class FakeUser32:
        def GetAsyncKeyState(self, vk):  # noqa: N802 - mirrors user32
            return 0

        def CallNextHookEx(self, *_a):  # noqa: N802
            return 42

        def keybd_event(self, *a):
            pass

    monkeypatch.setattr(hotkey, "_user32", FakeUser32())
    listener = hotkey.HotkeyListener()
    fired = []
    listener.triggered.connect(lambda: fired.append(True))

    def send(message, when):
        info = hotkey.KBDLLHOOKSTRUCT(vkCode=hotkey.VK_SNAPSHOT, scanCode=0, flags=0, time=when, dwExtraInfo=0)
        return listener._callback(0, message, ctypes.addressof(info))

    assert send(hotkey.WM_KEYDOWN, 1000) == 1
    for when in (1500, 1530, 1560):  # Windows repeating the held key: swallowed, not snips
        assert send(hotkey.WM_KEYDOWN, when) == 1
    assert fired == [True]
    assert send(hotkey.WM_KEYUP, 1600) == 42  # the key-up still reaches Windows
    send(hotkey.WM_KEYDOWN, 1700)
    assert fired == [True, True]  # pressed again after letting go
    send(hotkey.WM_KEYDOWN, 1700 + hotkey.REPEAT_GAP_MS + 1)  # its key-up went missing
    assert fired == [True, True, True]


def test_tray_cancel_snip_shows_only_while_snipping(forge, monkeypatch):
    from snipforge import app as app_module
    monkeypatch.setattr(app_module.CaptureSession, "start", lambda self: None)
    assert not forge._cancel_snip_action.isVisible()
    forge.capture()
    assert forge._cancel_snip_action.isVisible()
    forge.cancel_snip()
    assert forge._session is None and not forge._cancel_snip_action.isVisible()
