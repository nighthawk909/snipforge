"""Settings window: every control changes the real setting, and the Updates page follows the engine."""
import datetime as dt
from dataclasses import replace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QScrollArea

from snipforge import autostart, prefs
from snipforge.settings import app_settings
from snipforge.settings_window import SettingsWindow
from snipforge.update_controller import UpdateState
from snipforge.update_policy import PolicyInfo
from snipforge.update_prefs import UpdatePrefs
from snipforge.updates import Release


def release(version, prerelease=False, notes="Faster capture\nMore fixes", days=0):
    return Release(version, "", notes, f"SnipForge-{version}-setup.exe", "https://example.invalid/x", None,
                   prerelease, dt.datetime(2026, 10, 14) + dt.timedelta(days=days))


class FakeController(QObject):
    state_changed = Signal(object)
    releases_changed = Signal(object)

    def __init__(self, policy=PolicyInfo(), prefs_=UpdatePrefs()):
        super().__init__()
        self.policy, self.prefs = policy, prefs_
        self.state = UpdateState(kind="current", last_checked=dt.datetime(2026, 10, 16, 14, 12))
        self.releases = []
        self.busy = False
        self.calls = []
        self.blocker = ""

    def install_blocker(self, _release):
        return self.blocker

    def is_skipped(self, _release):
        return False

    def __getattr__(self, name):
        if name in ("check_now", "cancel", "install_ready", "install_version", "skip", "download"):
            return lambda *a: self.calls.append((name, *a))
        raise AttributeError(name)

    def set_prefs(self, p):
        self.calls.append(("set_prefs", p))
        self.prefs = p

    def emit(self, **state):
        self.state = UpdateState(**state)
        self.state_changed.emit(self.state)


class FakeHotkey:
    keys = None

    def set_keys(self, a, b):
        self.keys = (a, b)


@pytest.fixture(autouse=True)
def clean_prefs():
    keys = list(prefs.KEYS.values()) + ["beautify"]
    for key in keys:
        app_settings().remove(key)
    yield
    for key in keys:
        app_settings().remove(key)


@pytest.fixture
def make(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    made = []

    def build(**kwargs):
        kwargs.setdefault("update_controller", FakeController())
        w = SettingsWindow(library_root=tmp_path, **kwargs)
        made.append(w)
        return w

    yield build
    for w in made:
        w.close()


def test_sections_and_find(make):
    w = make()
    for key in ("general", "capture", "saving", "appearance", "updates", "about"):
        w.show_section(key)
        assert w.current_section() == key and w._nav[key].isChecked()
    w._find.setText("rollback")
    assert [k for k, item in w._nav.items() if not item.isHidden()] == ["updates"]
    w._find.setText("")
    assert all(not item.isHidden() for item in w._nav.values())


def test_general_and_capture_switches_take_effect(make, monkeypatch):
    calls = []
    monkeypatch.setattr(autostart, "set_enabled", calls.append)
    monkeypatch.setattr(autostart, "enabled_for_all_users", lambda: False)
    hotkey = FakeHotkey()
    w = make(hotkey=hotkey)
    w.startup.click()
    assert calls == [True]
    w.copy_on_capture.click()
    assert prefs.copy_on_capture() is False
    w.key_print_screen.click()
    assert prefs.hotkeys() == (True, False) and hotkey.keys == (True, False)
    w.magnifier.click()
    assert prefs.magnifier() is False


def test_saving_folder_format_and_beautify(make, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog
    w = make()
    (tmp_path / "Shots").mkdir()
    blocked = tmp_path / "not-a-folder.txt"
    blocked.write_text("x", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a: str(blocked))
    w._choose_folder()  # can't write there: refused with a message, nothing changes
    assert prefs.export_dir() == prefs.DEFAULT_EXPORT_DIR
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a: str(tmp_path / "Shots"))
    w._choose_folder()
    assert prefs.export_dir() == tmp_path / "Shots" and str(tmp_path / "Shots") in w.folder_row.description.text()
    w._set_folder(None)
    assert prefs.export_dir() == prefs.DEFAULT_EXPORT_DIR
    w.format.button("jpg").click()
    assert prefs.export_format() == "jpg"
    w.beautify.click()
    assert app_settings().value("beautify", False, bool) is True


def test_appearance_changes_the_theme(make, qapp):
    from snipforge.theme.manager import ACCENT_KEY, MODE_KEY, ThemeManager
    manager = ThemeManager(qapp, packs=[])
    try:
        w = make(theme_manager=manager)
        w.theme_tiles["dark"].click()
        assert manager.theme.mode == "dark" and w.theme_tiles["dark"].isChecked()
        w.accent_buttons["teal"].click()
        assert manager.accent_id == "teal"
        assert [k for k, b in w.accent_buttons.items() if b.isChecked()] == ["teal"]
    finally:
        manager.close()
        qapp.setStyleSheet("")
        app_settings().remove(MODE_KEY)
        app_settings().remove(ACCENT_KEY)


def test_updates_status_follows_the_engine(make):
    controller = FakeController()
    w = make(update_controller=controller)
    page = w.updates_page
    assert page._title.text() == "You're up to date" and "Oct 16" in page._last_checked.text()
    controller.emit(kind="checking")
    assert page._primary.text() == "Cancel" and not page._progress.isHidden()
    page._primary.click()
    assert controller.calls[-1] == ("cancel",)
    new = release("0.6.0")
    controller.emit(kind="ready", release=new)
    assert "0.6.0 is ready" in page._title.text() and page._primary.text() == "Restart and update"
    page._whats_new.click()
    assert not page._notes.isHidden() and "Faster capture" in page._notes.text()
    page._primary.click()
    assert controller.calls[-1] == ("install_ready",)
    page._skip.click()
    assert controller.calls[-1] == ("skip", "0.6.0")
    controller.blocker = "Your IT team updates SnipForge on this PC."
    controller.emit(kind="available", release=new)
    assert not page._primary.isEnabled() and "IT team" in page._message.text()


def test_install_mode_channel_and_pin(make):
    controller = FakeController()
    w = make(update_controller=controller)
    page = w.updates_page
    page._mode_buttons["auto"].click()
    assert controller.prefs.mode == "auto"
    page._channel.button("preview").click()
    assert controller.prefs.channel == "preview" and page._channel_row.title.text() == "Preview"
    page._pin.click()
    assert controller.prefs.pinned_version is not None
    assert controller.prefs.pinned_version in page._pin_row.description.text()


def test_policy_locks_what_it_sets(make):
    controller = FakeController(policy=PolicyInfo(mode="auto", channel="stable", source="feed",
                                                  feed=r"\\files\SnipForge"),
                                prefs_=UpdatePrefs(mode="auto"))
    page = make(update_controller=controller).updates_page
    assert not any(b.isEnabled() for b in page._mode_buttons.values()) and not page._mode_note.isHidden()
    assert not page._channel.isEnabled()
    assert page._source_row.title.text() == "Managed by your organization"
    assert r"\\files\SnipForge" in page._source_row.description.text()
    off = FakeController(policy=PolicyInfo(disabled=True))
    page = make(update_controller=off).updates_page
    assert "managed by your organization" in page._title.text() and not page._check.isEnabled()


def test_version_list_installs_and_rolls_back(make, monkeypatch):
    from snipforge import updates_page
    controller = FakeController()
    page = make(update_controller=controller).updates_page
    monkeypatch.setattr(updates_page, "__version__", "0.5.0")
    page._current = "0.5.0"
    controller.releases = [release("0.7.0-preview.1", prerelease=True, days=3), release("0.6.0", days=2),
                           release("0.5.0"), release("0.4.0", days=-1)]
    controller.releases_changed.emit(controller.releases)
    rows = [page._versions.itemAt(n).widget() for n in range(page._versions.count())]
    assert [r.title.text() for r in rows] == ["0.7.0-preview.1", "0.6.0", "0.5.0", "0.4.0"]
    assert "Preview" in rows[0].description.text() and "Latest" in rows[1].description.text()
    assert "Installed" in rows[2].description.text() and rows[2].control is None
    install = rows[1].control
    assert isinstance(install, QPushButton) and install.text() == "Install"
    install.click()
    assert controller.calls[-1] == ("install_version", "0.6.0")
    back = rows[3].control
    assert back.text() == "Go back to this version"
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.No)
    back.click()
    assert controller.calls[-1] == ("install_version", "0.6.0")  # cancelled
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.Yes)
    back.click()
    assert controller.calls[-1] == ("install_version", "0.4.0")
    controller.policy = replace(controller.policy, allow_rollback=False)
    controller.releases_changed.emit(controller.releases)
    rows = [page._versions.itemAt(n).widget() for n in range(page._versions.count())]
    back = rows[3].control  # IT turned rollback off: still shown, with the reason
    assert not back.isEnabled() and "IT team" in back.toolTip()


def test_version_buttons_follow_the_engine_state(make):
    controller = FakeController()
    page = make(update_controller=controller).updates_page
    page._current = "0.5.0"
    controller.releases = [release("0.6.0", days=2), release("0.5.0")]
    controller.busy = True  # the list arrives while the check is still running
    controller.releases_changed.emit(controller.releases)
    install = page._versions.itemAt(0).widget().control
    assert not install.isEnabled() and "Wait" in install.toolTip()
    controller.busy = False
    controller.emit(kind="available", release=controller.releases[0])
    assert install.isEnabled() and install.toolTip() == ""
    controller.emit(kind="installing", release=controller.releases[0])
    assert not install.isEnabled()


@pytest.mark.parametrize("height", [760, 520])
def test_every_page_opens_at_the_top_and_short_pages_dont_scroll(make, height):
    from snipforge.settings_window import SECTIONS
    controller = FakeController()
    controller.releases = [release("0.8.0", days=7), release("0.7.0", days=3), release("0.6.0")]
    w = make(update_controller=controller)
    controller.releases_changed.emit(controller.releases)  # a long Updates page
    w.resize(1040, height)
    w.show()
    QApplication.processEvents()
    bar = w.findChild(QScrollArea, "SettingsScroll").verticalScrollBar()
    for previous in SECTIONS:
        for key in SECTIONS:
            w.show_section(previous)
            QApplication.processEvents()
            bar.setValue(bar.maximum())  # you'd scrolled down the page you were on
            w.show_section(key)
            QApplication.processEvents()
            assert bar.value() == 0, (previous, key)
            if key == "updates":
                assert bar.maximum() > 0  # the long page really scrolls, so the reset is tested
            elif height == 760 and key != "capture":  # short pages fit the default window (Capture,
                # with its shortcuts and capture presets, is a long page now)
                assert bar.maximum() == 0, (key, bar.maximum())
    w.show_section("updates")
    QApplication.processEvents()
    bar.setValue(bar.maximum())
    assert bar.value() > 0
    w.hide()  # reopening the window from the tray starts at the top too
    w.show_section("updates")
    w.show()
    QApplication.processEvents()
    assert bar.value() == 0


def test_changing_page_never_puts_the_keyboard_in_the_find_box(make):
    w = make()
    w.show()
    w.activateWindow()
    QApplication.processEvents()
    w.startup.setFocus()  # e.g. you clicked a switch on General
    QApplication.processEvents()
    w.show_section("capture")
    QApplication.processEvents()
    assert QApplication.focusWidget() is not w._find
