"""The Screen Recording check before a snip (docs/MAC-PLAN.md phase 1): Windows never asks; a Mac
without the permission explains it and offers System Settings instead of a useless snip."""
import sys
import types
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QSystemTrayIcon

from snipforge import app, prefs, screen_access
from snipforge.library import Library


@pytest.fixture
def macos(monkeypatch, on_a_mac):
    """A Mac whose Quartz answers what the test says; records what was asked of it."""
    on_a_mac(screen_access)
    fake = types.ModuleType("snipforge._screen_access_mac")
    fake.allowed, fake.requests = False, []
    fake.preflight = lambda: fake.allowed
    fake.request = lambda: fake.requests.append(True) or False
    monkeypatch.setitem(sys.modules, "snipforge._screen_access_mac", fake)
    import snipforge
    monkeypatch.setattr(snipforge, "_screen_access_mac", fake, raising=False)
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    fake.opened = opened
    return fake


def test_windows_never_asks(monkeypatch):
    monkeypatch.setattr(screen_access, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(screen_access, "_ask", lambda parent=None: pytest.fail("asked on Windows"))
    assert screen_access.asks() is False and screen_access.granted() is True
    assert screen_access.ready_to_snip() is True


def test_a_mac_with_the_permission_snips_straight_away(macos, monkeypatch):
    macos.allowed = True
    monkeypatch.setattr(screen_access, "_ask", lambda parent=None: pytest.fail("asked with the permission"))
    assert screen_access.ready_to_snip() is True and macos.requests == []


def test_without_it_open_system_settings_asks_macos_and_opens_the_page(macos, monkeypatch):
    monkeypatch.setattr(screen_access, "_ask", lambda parent=None: screen_access.OPEN)
    assert screen_access.ready_to_snip() is False  # no snip: it would show little but the desktop
    assert macos.requests == [True]  # SnipForge is in the list, ready to switch on
    assert macos.opened == [screen_access.SETTINGS_URL]
    assert "Privacy_ScreenCapture" in screen_access.SETTINGS_URL


@pytest.mark.parametrize(("choice", "snips"), [(screen_access.ANYWAY, True), (screen_access.CANCEL, False)])
def test_without_it_the_person_decides(macos, monkeypatch, choice, snips):
    monkeypatch.setattr(screen_access, "_ask", lambda parent=None: choice)
    assert screen_access.ready_to_snip() is snips
    assert macos.requests == [] and macos.opened == []


def test_a_check_that_fails_lets_the_snip_go_ahead(macos, monkeypatch):
    def broken():
        raise AttributeError("CGPreflightScreenCaptureAccess")

    macos.preflight = broken
    monkeypatch.setattr(screen_access, "_ask", lambda parent=None: pytest.fail("asked when macOS couldn't say"))
    assert screen_access.ready_to_snip() is True


def test_the_explanation_says_how_and_offers_three_ways_out(qapp, monkeypatch):
    seen = {}

    def answer(box):
        seen["text"] = box.text() + " " + box.informativeText()
        seen["buttons"] = [b.text() for b in box.buttons()]
        next(b for b in box.buttons() if b.text() == "Snip anyway").click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", answer)
    assert screen_access._ask() == screen_access.ANYWAY
    assert "Screen & System Audio Recording" in seen["text"] and "quit SnipForge" in seen["text"]
    assert {"Open System Settings", "Snip anyway"} <= set(seen["buttons"]) and len(seen["buttons"]) == 3
    monkeypatch.setattr(QMessageBox, "exec", lambda box: 0)  # closed without a choice
    assert screen_access._ask() == screen_access.CANCEL


@pytest.fixture
def forge(qapp, tmp_path, monkeypatch, no_dialogs):
    monkeypatch.setattr(app.UpdateFlow, "start", lambda self: None)  # no update checks in unit tests
    monkeypatch.setattr(QSystemTrayIcon, "showMessage", lambda self, *a: None)
    started = []
    monkeypatch.setattr(app.SnipForgeApp, "_start", lambda self, session: started.append(session))
    forge = app.SnipForgeApp(qapp, Library(tmp_path))
    forge.started = started
    yield forge
    forge._tray.hide()
    forge.indexer.close()


def test_no_snip_starts_until_macos_lets_snipforge_see_the_screen(forge, monkeypatch):
    monkeypatch.setattr(app.screen_access, "ready_to_snip", lambda parent=None: False)
    monkeypatch.setattr(app, "on_screen", lambda area: True)
    monkeypatch.setattr(prefs, "last_area", lambda: prefs.LastArea("x", (0, 0, 100, 100), 1.0, (0, 0, 50, 50)))
    forge.capture()
    forge.repeat_last()
    forge.record()
    assert forge.started == []


def test_with_it_snips_start_as_before(forge, monkeypatch):
    monkeypatch.setattr(app.screen_access, "ready_to_snip", lambda parent=None: True)
    forge.capture()
    assert len(forge.started) == 1


@pytest.mark.mac
def test_macos_answers_the_check_without_asking():
    """The real Quartz call on the runner: an answer either way (CI can't grant the permission), and
    no prompt, since only Open System Settings asks."""
    from snipforge import _screen_access_mac

    assert _screen_access_mac.preflight() in (True, False)
