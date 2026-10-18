"""Opening SnipForge again from Finder or the Dock brings up the Library on a Mac (docs/MAC-PLAN.md
phase 1), the way a second launch does on Windows."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from snipforge import app, reopen
from snipforge.library import Library


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def mac(on_a_mac, monkeypatch):
    on_a_mac(reopen)
    monkeypatch.setattr(reopen, "_cocoa", lambda: True)  # tests run on Qt's offscreen platform


@pytest.fixture
def watcher(qapp, mac, monkeypatch):
    clock = Clock()
    shown = {"windows": False}
    monkeypatch.setattr(reopen, "_windows_shown", lambda _app: shown["windows"])
    w = reopen.ReopenWatcher(qapp, clock=clock)
    assert w.active
    w.clock, w.shown, w.got = clock, shown, []
    w.reopened.connect(lambda: w.got.append(True))
    w.state_changed(Qt.ApplicationInactive)
    clock.now += reopen.STARTUP_GRACE_S + 1
    yield w
    w.stop()


def test_a_dock_click_while_snipforge_is_in_front_brings_up_the_library(watcher):
    watcher.shown["windows"] = True
    watcher.state_changed(Qt.ApplicationActive)  # activated by a click on an editor, say
    assert watcher.got == []
    watcher.clock.now += 5
    watcher.state_changed(Qt.ApplicationActive)  # Qt passes the Dock's reopen on as active again
    assert watcher.got == [True]


def test_opened_again_with_nothing_on_screen_brings_up_the_library(watcher):
    watcher.state_changed(Qt.ApplicationActive)
    assert watcher.got == [True]


def test_one_dock_click_brings_it_up_once(watcher):
    """With nothing on screen, a Dock click arrives as the activation and then as Qt's reopen."""
    watcher.state_changed(Qt.ApplicationActive)
    watcher.clock.now += 0.05
    watcher.state_changed(Qt.ApplicationActive)
    assert watcher.got == [True]
    watcher.clock.now += reopen.SAME_REOPEN_S + 1  # a second click, later on
    watcher.state_changed(Qt.ApplicationActive)
    assert watcher.got == [True, True]


def test_clicking_one_of_its_windows_leaves_it_be(watcher):
    watcher.shown["windows"] = True  # an editor, a snip's overlay, Settings...
    watcher.state_changed(Qt.ApplicationActive)
    watcher.state_changed(Qt.ApplicationInactive)
    assert watcher.got == []


def test_starting_up_is_the_command_line_s_business(qapp, mac, monkeypatch):
    """Launched with --background at sign-in, macOS activating it mustn't open the Library."""
    clock = Clock()
    monkeypatch.setattr(reopen, "_windows_shown", lambda _app: False)
    w = reopen.ReopenWatcher(qapp, clock=clock)
    got = []
    w.reopened.connect(lambda: got.append(True))
    try:
        w.state_changed(Qt.ApplicationActive)
        w.state_changed(Qt.ApplicationActive)
        assert got == []
    finally:
        w.stop()


def test_watches_only_a_mac_s_window_server(qapp, monkeypatch):
    """Windows hands over through a second launch instead, and Qt's offscreen platform (the tests)
    has no Dock: neither watches the app's activation."""
    signal = "2applicationStateChanged(Qt::ApplicationState)"
    before = qapp.receivers(signal)
    with monkeypatch.context() as windows:
        windows.setattr(reopen, "_cocoa", lambda: True)
        windows.setattr(reopen, "sys", SimpleNamespace(platform="win32"))
        assert reopen.ReopenWatcher(qapp).active is False
    with monkeypatch.context() as offscreen_mac:
        offscreen_mac.setattr(reopen, "sys", SimpleNamespace(platform="darwin"))
        assert qapp.platformName() != "cocoa"  # what the test suite runs on
        assert reopen.ReopenWatcher(qapp).active is False
    assert qapp.receivers(signal) == before


def test_only_real_windows_count(qapp):
    assert reopen._windows_shown(qapp) is False
    menu = QMenu()
    menu.addAction("New snip")
    menu.popup(menu.pos())  # the menu bar icon's menu, open
    try:
        assert reopen._windows_shown(qapp) is False
        window = QWidget()
        window.show()
        assert reopen._windows_shown(qapp) is True
        window.close()
    finally:
        menu.close()


def test_snipforge_shows_its_library_when_reopened(qapp, tmp_path, monkeypatch, no_dialogs):
    monkeypatch.setattr(app.UpdateFlow, "start", lambda self: None)
    monkeypatch.setattr(QSystemTrayIcon, "showMessage", lambda self, *a: None)
    shown = []
    monkeypatch.setattr(app.SnipForgeApp, "show_home", lambda self: shown.append(True))
    forge = app.SnipForgeApp(qapp, Library(tmp_path))
    try:
        forge._reopen.reopened.emit()
        assert shown == [True]
    finally:
        forge._tray.hide()
        forge.indexer.close()
