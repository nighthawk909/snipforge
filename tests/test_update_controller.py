"""UpdateController (the engine the Settings window drives) and the tray's UpdateFlow on top of it.

The update source, installer launch and app quit are faked; settings go to the test INI file.
"""
import datetime as dt
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QMessageBox, QSystemTrayIcon

from snipforge import project, update_controller, update_flow, update_sources, updates
from snipforge.settings import app_settings
from snipforge.update_controller import UpdateController, UpdateState, check_is_due
from snipforge.update_policy import PolicyInfo, parse_policy
from snipforge.update_prefs import UpdatePrefs, load_prefs, save_prefs

UPDATE_KEYS = ("auto_update", "update_mode", "update_channel", "update_pinned_version", "skip_version",
               "last_update_check", "update_ready", "update_installed")


def rel(version, notes="What's new", prerelease=None):
    name = f"SnipForge-{version}-setup.exe"
    base = f"{project.RELEASES_URL}/download/v{version}/"
    return updates.Release(version, f"{project.RELEASES_URL}/tag/v{version}", notes,
                           name, base + name, base + "SHA256SUMS.txt",
                           prerelease="-" in version if prerelease is None else prerelease,
                           published=dt.datetime(2026, 10, 1, tzinfo=dt.UTC))


def spin(until, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if until():
            return True
        time.sleep(0.005)
    return until()


class Stub:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeSource(update_sources.UpdateSource):
    label = "the test feed"

    def __init__(self, releases=(), payload=b"installer bytes"):
        self.releases = list(releases)
        self.payload = payload
        self.fetch_error = self.download_error = ""
        self.fetches = 0
        self.downloads = []
        self.hold = False
        self.held = []

    def fetch(self, done):
        self.fetches += 1
        if self.fetch_error:
            done(None, self.fetch_error)
        else:
            done(list(self.releases), "")
        return Stub()

    def download(self, release, folder, done, progress=None):
        self.downloads.append(release.version)

        def finish():
            if self.download_error:
                done(None, self.download_error)
                return
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / release.installer_name
            path.write_bytes(self.payload)
            done(path, "")

        if progress:
            progress(50, 100)
        if self.hold:
            self.held.append(finish)
        else:
            finish()
        return Stub()


class Clock:
    def __init__(self):
        self.now = dt.datetime(2026, 10, 16, 9, 0)

    def __call__(self):
        return self.now

    def advance(self, hours=24):
        self.now += dt.timedelta(hours=hours)


@pytest.fixture
def clean_settings():
    def wipe():
        settings = app_settings()
        for key in UPDATE_KEYS:
            settings.remove(key)
        settings.sync()

    wipe()
    yield
    wipe()


@pytest.fixture
def make(qapp, tmp_path, clean_settings, monkeypatch):
    monkeypatch.setattr(update_controller, "QUIT_DELAY_MS", 0)
    monkeypatch.setattr(update_controller, "STARTUP_INSTALL_DELAY_MS", 0)
    downloads = tmp_path / "downloads"

    def factory(releases=(), *, current="0.5.0", policy=None, install_mode="per-user", mode=None,
                source="fake", clock=None):
        if mode:
            save_prefs(replace(load_prefs(), mode=mode))
        h = SimpleNamespace(source=FakeSource(releases) if source == "fake" else source, launched=[], quits=[],
                            states=[], lists=[], dir=downloads, clock=clock or Clock())
        h.c = UpdateController(
            policy=policy or PolicyInfo(), source=h.source, download_dir=downloads, current_version=current,
            install_mode=install_mode, launcher=lambda path, relaunch: h.launched.append((path, relaunch)) or "",
            quit_app=lambda: h.quits.append(True), clock=h.clock)
        h.c.state_changed.connect(h.states.append)
        h.c.releases_changed.connect(h.lists.append)
        h.kinds = lambda: [k for i, k in enumerate(s.kind for s in h.states) if i == 0 or h.states[i - 1].kind != k]
        return h

    return factory


# --- the state value ----------------------------------------------------------------------
def test_state_is_validated():
    assert UpdateState() == UpdateState(kind="idle", release=None, progress=0, message="", last_checked=None)
    with pytest.raises(ValueError):
        UpdateState(kind="bogus")
    with pytest.raises(ValueError):
        UpdateState(progress=101)


def test_check_is_due():
    now = dt.datetime(2026, 10, 14, 12, 0)
    assert check_is_due(now, None)
    assert check_is_due(now, "garbage")
    assert not check_is_due(now, "2026-10-14T08:00:00")
    assert check_is_due(now, "2026-10-13T08:00:00")
    assert not check_is_due(now, dt.datetime(2026, 10, 14, 8, 0))
    assert update_flow.check_is_due is check_is_due


# --- checking -----------------------------------------------------------------------------
def test_check_reports_up_to_date_and_lists_releases(make):
    h = make([rel("0.5.0"), rel("0.4.0")])
    h.c.check_now()
    assert h.kinds() == ["checking", "current"]
    assert h.c.state.last_checked == h.clock.now and "0.5.0" in h.c.state.message
    assert [r.version for r in h.lists[-1]] == ["0.5.0", "0.4.0"] == [r.version for r in h.c.releases]


def test_check_errors_are_reported(make):
    h = make()
    h.source.fetch_error = "offline"
    h.c.check_now()
    assert h.c.state.kind == "error" and "offline" in h.c.state.message and h.c.state.last_checked is None


def test_ask_mode_downloads_in_background_then_waits(make):
    h = make([rel("0.6.0"), rel("0.5.0")], mode="ask")
    h.c.check_now()
    assert h.kinds() == ["checking", "downloading", "ready"]
    assert any(s.kind == "downloading" and s.progress == 50 for s in h.states)
    assert h.c.state.release.version == "0.6.0" and h.c.state.progress == 100
    assert h.source.downloads == ["0.6.0"] and h.launched == []


def test_manual_mode_never_checks_by_itself(make):
    h = make([rel("0.6.0")], mode="manual")
    h.c._automatic_check()
    assert h.source.fetches == 0
    h.c.check_now()
    assert h.kinds() == ["checking", "available"] and h.source.downloads == []
    h.c.download(h.c.state.release)
    assert h.c.state.kind == "ready" and h.launched == []


def test_automatic_checks_run_at_most_daily(make):
    h = make([rel("0.5.0")], mode="ask")
    h.c._automatic_check()
    h.c._automatic_check()
    assert h.source.fetches == 1
    h.clock.advance(hours=21)
    h.c._automatic_check()
    assert h.source.fetches == 2


def test_managed_installs_are_offered_not_downloaded(make):
    h = make([rel("0.6.0")], install_mode="all-users", mode="ask")
    h.c.check_now()
    assert h.kinds() == ["checking", "available"] and h.source.downloads == []
    assert "IT" in h.c.state.message and h.c.install_blocker(h.c.state.release)


# --- installing ---------------------------------------------------------------------------
def test_auto_mode_installs_on_next_start_and_shows_what_changed(make):
    first = make([rel("0.6.0", notes="Faster crop"), rel("0.5.0")], mode="auto")
    first.c.check_now()
    assert first.c.state.kind == "ready" and "next time" in first.c.state.message
    second = make(mode="auto")
    second.c.start()
    assert spin(lambda: second.quits)
    assert second.launched == [(first.dir / "SnipForge-0.6.0-setup.exe", True)]
    assert second.c.state.kind == "installing"
    third = make(current="0.6.0")
    third.c.start()
    assert third.c.just_updated.version == "0.6.0" and third.c.just_updated.notes == "Faster crop"
    assert third.c.state.kind == "current" and "0.6.0" in third.c.state.message
    assert not any(third.dir.glob("*.exe"))  # the used installer is cleaned up
    fourth = make(current="0.6.0")
    fourth.c.start()
    assert fourth.c.just_updated is None  # shown once


def test_ask_mode_waits_at_next_start(make):
    first = make([rel("0.6.0")], mode="ask")
    first.c.check_now()
    second = make(mode="ask")
    second.c.start()
    assert second.c.state.kind == "ready" and second.c.state.release.version == "0.6.0"
    spin(lambda: False, timeout=0.05)
    assert second.launched == []


def test_auto_mode_installs_on_quit_without_relaunching(make):
    h = make([rel("0.6.0")], mode="auto")
    h.c.check_now()
    h.c._on_session_end()
    h.c.install_on_quit()
    assert h.launched == []  # never while Windows is signing out
    h.c._session_end_at -= update_controller.SESSION_END_GRACE_S + 1  # that sign-out was called off
    h.c.install_on_quit()
    assert h.launched == [(h.dir / "SnipForge-0.6.0-setup.exe", False)] and h.quits == []
    h.c.install_on_quit()
    assert len(h.launched) == 1
    ask = make([rel("0.6.0")], mode="ask")
    ask.c.check_now()
    ask.c.install_on_quit()
    assert ask.launched == []


def test_install_ready_rechecks_the_downloaded_file(make):
    h = make([rel("0.6.0")], mode="ask")
    h.c.check_now()
    (h.dir / "SnipForge-0.6.0-setup.exe").write_bytes(b"swapped")
    h.c.install_ready()
    assert h.c.state.kind == "error" and "changed" in h.c.state.message and h.launched == []
    h.c.install_ready()
    assert h.c.state.kind == "error" and h.launched == []


def test_failed_installer_launch_is_reported(make):
    h = make([rel("0.6.0")], mode="ask")
    h.c._launcher = lambda path, relaunch: "Access is denied"
    h.c.check_now()
    h.c.install_ready()
    assert h.c.state.kind == "error" and "Access is denied" in h.c.state.message and h.quits == []
    assert not app_settings().contains("update_installed")


def test_an_unfinished_update_is_reported_at_next_start(make):
    h = make([rel("0.6.0")], mode="ask")
    h.c.check_now()
    h.c.install_ready()
    assert h.launched
    soon = Clock()
    soon.advance(hours=0.05)
    meanwhile = make(current="0.5.0", clock=soon)
    meanwhile.c.start()  # started again while the installer may still be running: say nothing yet
    assert meanwhile.c.state.kind == "idle" and app_settings().contains("update_installed")
    later = Clock()
    later.advance(hours=1)
    again = make(current="0.5.0", clock=later)
    again.c.start()
    assert again.c.state.kind == "error" and "didn't finish" in again.c.state.message
    assert again.c.just_updated is None and not app_settings().contains("update_installed")


def test_a_rollback_that_didnt_happen_releases_the_pin(make):
    save_prefs(UpdatePrefs(pinned_version="0.6.0"))
    h = make([rel("0.6.0"), rel("0.5.0")], current="0.6.0")
    h.c.check_now()
    h.c.install_version("0.5.0")
    assert h.launched and load_prefs().pinned_version == "0.5.0"
    later = Clock()
    later.advance(hours=1)
    blocked = make(current="0.6.0", clock=later)  # e.g. the installer was blocked by antivirus
    blocked.c.start()
    assert blocked.c.state.kind == "error" and load_prefs().pinned_version == "0.6.0"


def test_installing_fences_off_a_check_in_flight(make):
    h = make([rel("0.6.0")], mode="ask")
    h.c.check_now()
    assert h.c.state.kind == "ready"
    pending = []
    h.source.fetch = lambda done: pending.append(done) or Stub()
    h.clock.advance()
    h.c._automatic_check()
    assert h.c.state.kind == "checking"
    h.c.install_ready()
    assert h.c.state.kind == "installing" and h.launched
    h.source.releases = [rel("0.7.0")]
    pending[0]([rel("0.7.0")], "")  # the check finishing late changes nothing
    assert h.c.state.kind == "installing" and (h.dir / "SnipForge-0.6.0-setup.exe").exists()
    h.c.download(rel("0.7.0"))
    h.c.install_version("0.7.0")
    h.c.skip("0.6.0")
    assert h.c.state.kind == "installing" and h.source.downloads == ["0.6.0"]
    assert (h.dir / "SnipForge-0.6.0-setup.exe").exists()


def test_a_feed_that_republishes_a_version_is_downloaded_again(make):
    first = replace(rel("0.6.0"), sums_url=None, sha256="a" * 64)
    h = make([first], mode="ask")
    h.c.check_now()
    assert h.source.downloads == ["0.6.0"]
    h.source.releases = [replace(first, sha256="b" * 64)]
    h.c.check_now()
    assert h.source.downloads == ["0.6.0", "0.6.0"]


def test_restored_downloads_are_rechecked(make, tmp_path):
    h = make([rel("0.6.0")], mode="ask")
    h.c.check_now()
    record = json.loads(app_settings().value("update_ready"))
    outside = tmp_path / "SnipForge-0.6.0-setup.exe"
    outside.write_bytes(b"installer bytes")
    app_settings().setValue("update_ready", json.dumps({**record, "path": str(outside)}))
    again = make(mode="auto")
    again.c.start()
    spin(lambda: False, timeout=0.05)
    assert again.c.state.kind == "idle" and again.launched == []
    app_settings().setValue("update_ready", "not json")
    make(mode="auto").c.start()


# --- channel, pin, skip, rollback ---------------------------------------------------------
def test_preview_channel_includes_prereleases(make):
    h = make([rel("0.6.0-beta.1"), rel("0.5.1"), rel("0.5.0")], mode="manual")
    h.c.check_now()
    assert h.c.state.release.version == "0.5.1"
    assert [r.version for r in h.c.releases] == ["0.5.1", "0.5.0"]
    h.c.set_prefs(replace(h.c.prefs, channel="preview"))
    assert [r.version for r in h.lists[-1]] == ["0.6.0-beta.1", "0.5.1", "0.5.0"]
    assert h.c.state.kind == "available" and h.c.state.release.version == "0.6.0-beta.1"
    assert h.source.fetches == 1  # re-filtered without going online


def test_pinned_version_holds_updates(make):
    save_prefs(UpdatePrefs(mode="ask", pinned_version="0.5.0"))
    h = make([rel("0.7.0"), rel("0.6.0"), rel("0.5.0")])
    h.c.check_now()
    assert h.c.state.kind == "current" and "0.5.0" in h.c.state.message and h.source.downloads == []
    h.c.set_prefs(replace(h.c.prefs, pinned_version="0.6.0"))
    assert h.c.state.kind == "available" and h.c.state.release.version == "0.6.0" and h.source.downloads == []
    h.c.check_now()
    assert h.source.downloads == ["0.6.0"] and h.c.state.kind == "ready"
    h.c.set_prefs(replace(h.c.prefs, pinned_version="0.5.0"))
    assert h.c.state.kind == "current" and not any(h.dir.glob("*.exe"))


def test_skip_this_version(make):
    h = make([rel("0.6.0"), rel("0.5.0")], mode="ask")
    h.c.check_now()
    h.c.skip("0.6.0")
    assert load_prefs().skip_version == "0.6.0" and not any(h.dir.glob("*.exe"))
    assert h.c.state.kind == "available" and h.c.is_skipped(h.c.state.release)
    h.clock.advance()
    h.c._automatic_check()
    assert h.source.fetches == 2 and h.source.downloads == ["0.6.0"] and h.c.state.kind == "available"
    h.c.check_now()  # asking explicitly still offers it
    assert h.source.downloads == ["0.6.0", "0.6.0"]
    h.source.releases.insert(0, rel("0.6.1"))
    h.clock.advance()
    h.c._automatic_check()
    assert h.source.downloads[-1] == "0.6.1"


def test_rollback_installs_the_older_version_and_pins_it(make):
    h = make([rel("0.6.0"), rel("0.5.0")], current="0.6.0", mode="ask")
    h.c.check_now()
    older = h.c.releases[1]
    assert h.c.install_blocker(older) == ""
    h.c.install_version("0.5.0")
    assert h.source.downloads == ["0.5.0"] and h.launched == [(h.dir / "SnipForge-0.5.0-setup.exe", True)]
    assert load_prefs().pinned_version == "0.5.0" and h.c.state.kind == "installing"
    assert spin(lambda: h.quits)


def test_rollback_can_be_blocked_by_policy(make):
    h = make([rel("0.6.0"), rel("0.5.0")], current="0.6.0", policy=PolicyInfo(allow_rollback=False))
    h.c.check_now()
    h.c.install_version("0.5.0")
    assert h.c.state.kind == "error" and "older" in h.c.state.message
    assert h.source.downloads == [] and load_prefs().pinned_version is None


def test_installing_past_the_pin_moves_it(make):
    save_prefs(UpdatePrefs(pinned_version="0.5.0"))
    h = make([rel("0.6.0"), rel("0.5.0")])
    h.c.check_now()
    h.c.install_version("0.6.0")
    assert load_prefs().pinned_version == "0.6.0" and h.launched


def test_install_version_that_is_unknown_or_current(make):
    h = make([rel("0.5.0")])
    h.c.check_now()
    h.c.install_version("0.9.9")
    assert h.c.state.kind == "error" and "0.9.9" in h.c.state.message
    h.c.install_version("0.5.0")
    assert h.c.state.kind == "current" and h.source.downloads == []


def test_cancel_a_download(make):
    h = make([rel("0.6.0")], mode="manual")
    h.source.hold = True
    h.c.check_now()
    h.c.download(h.c.state.release)
    assert h.c.state.kind == "downloading" and h.c.busy
    h.c.cancel()
    assert h.c.state.kind == "available" and not h.c.busy
    h.source.held[0]()  # a result arriving after cancelling is ignored
    assert h.c.state.kind == "available"


def test_download_failure_is_reported(make):
    h = make([rel("0.6.0")], mode="ask")
    h.source.download_error = "The download didn't match its checksum, so it wasn't installed."
    h.c.check_now()
    assert h.c.state.kind == "error" and "checksum" in h.c.state.message and h.c.state.release.version == "0.6.0"


# --- policy -------------------------------------------------------------------------------
def test_policy_can_turn_updates_off(make):
    h = make([rel("0.6.0")], policy=PolicyInfo(disabled=True), mode="ask")
    h.c.start()
    h.c.check_now()
    assert h.source.fetches == 0 and h.c.state.kind == "idle" and "organization" in h.c.state.message
    assert h.c.install_blocker(rel("0.6.0")) and not h.c._timer.isActive()


def test_policy_forced_values_win_but_are_not_saved(make):
    save_prefs(UpdatePrefs(mode="ask", channel="stable"))
    h = make(policy=PolicyInfo(mode="manual", channel="preview"))
    assert h.c.prefs.mode == "manual" and h.c.prefs.channel == "preview"
    h.c.set_prefs(UpdatePrefs(mode="auto", channel="stable", pinned_version="0.5.0"))
    assert h.c.prefs == UpdatePrefs(mode="manual", channel="preview", pinned_version="0.5.0")
    assert load_prefs() == UpdatePrefs(mode="ask", channel="stable", pinned_version="0.5.0")
    with pytest.raises(TypeError):
        h.c.set_prefs({"mode": "auto"})


def test_unusable_feed_policy_is_an_error(make):
    policy = parse_policy([{"UpdateSource": "feed"}, {}])
    h = make(policy=policy, source=None)
    assert h.c.policy is policy
    h.c.check_now()
    assert h.c.state.kind == "error" and "UpdateFeed" in h.c.state.message


# --- the tray flow ------------------------------------------------------------------------
@pytest.fixture
def tray(make, monkeypatch):
    messages, boxes, icons, opened = [], [], [], []
    for name in ("information", "warning", "question", "critical"):  # every dialog must be plain text
        monkeypatch.setattr(QMessageBox, name, lambda *a: pytest.fail("static QMessageBox used"))
    monkeypatch.setattr(QSystemTrayIcon, "showMessage", lambda self, title, text, *a: messages.append(text))
    from PySide6.QtGui import QDesktopServices
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))

    def build(*args, answer=None, **kwargs):
        h = make(*args, **kwargs)
        h.flow = update_flow.UpdateFlow(QSystemTrayIcon(), controller=h.c)
        h.messages, h.boxes, h.icons, h.opened = messages, boxes, icons, opened
        h.answer = lambda label: choose(monkeypatch, label, boxes, icons)
        h.answer(answer)
        return h

    return build


def choose(monkeypatch, label, boxes, icons):
    """Answer every update dialog by clicking the button with this label (None: no button)."""
    def exec_(box):
        assert box.textFormat() == Qt.PlainText  # release text from the network is never rendered as HTML
        boxes.append(box.text())
        icons.append(box.icon())
        box._clicked = next((b for b in box.buttons() if b.text() == label), None)
        return 0

    monkeypatch.setattr(QMessageBox, "exec", exec_)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: box._clicked)


def pump():
    """Run the dialogs UpdateFlow defers until every listener has seen the state."""
    for _ in range(5):
        QCoreApplication.processEvents()


def test_tray_check_in_manual_mode_offers_and_installs(tray):
    h = tray([rel("0.6.0")], mode="manual", answer="Update now")
    h.flow.check_now()
    assert "Checking for updates…" in h.messages and h.boxes == []  # not inside the controller's signal
    pump()
    assert h.boxes == ["SnipForge 0.6.0 is available (you have 0.5.0)."]
    assert h.launched and any("reopen" in m for m in h.messages)


def test_tray_ask_mode_asks_once_the_download_is_ready(tray):
    h = tray([rel("0.6.0")], mode="ask", answer="Install now")
    h.c._automatic_check()
    pump()
    assert h.boxes == ["SnipForge 0.6.0 is ready to install (you have 0.5.0)."] and h.launched


def test_tray_ask_mode_skip_and_later(tray):
    h = tray([rel("0.6.0")], mode="ask", answer="Later")
    h.c._automatic_check()
    pump()
    assert h.c.state.kind == "ready" and h.launched == [] and len(h.boxes) == 1
    h.answer("Skip this version")
    h.flow.check_now()
    pump()
    assert load_prefs().skip_version == "0.6.0" and h.c.state.kind == "available" and h.launched == []
    boxes = len(h.boxes)
    h.clock.advance()
    h.c._automatic_check()
    pump()
    assert len(h.boxes) == boxes  # a skipped version isn't brought up again automatically


def test_tray_dialogs_dont_stack_or_go_stale(tray):
    h = tray([rel("0.6.0")], mode="ask", answer="Later")
    h.flow._dialog_open = True  # a prompt is already up
    h.c._automatic_check()
    pump()
    assert h.boxes == []
    h.flow._dialog_open = False
    h.clock.advance()
    h.c._automatic_check()
    h.c.skip("0.6.0")  # the situation changed before the prompt could open
    pump()
    assert h.boxes == []


def test_tray_auto_mode_installs_later_without_asking(tray):
    h = tray([rel("0.6.0")], mode="auto", answer="Install now")
    h.c._automatic_check()
    pump()
    assert h.boxes == [] and h.launched == [] and any("next time" in m for m in h.messages)


def test_tray_managed_install_opens_the_download_page(tray):
    h = tray([rel("0.6.0")], mode="ask", install_mode="all-users", answer="Open download page")
    h.c._automatic_check()
    pump()
    assert h.opened == [f"{project.RELEASES_URL}/tag/v0.6.0"] and h.launched == []


def test_tray_progress_dialog_for_a_chosen_install(tray):
    h = tray([rel("0.6.0")], mode="manual", answer="Update now")
    h.source.hold = True
    h.flow.check_now()
    pump()
    assert h.flow._progress is not None and h.flow._progress.isVisible()
    h.source.held[0]()
    assert h.flow._progress is None and h.launched


def test_tray_check_messages(tray):
    h = tray([rel("0.5.0")], answer="OK")
    h.flow.check_now()
    pump()
    assert h.icons[-1] == QMessageBox.Information and "0.5.0" in h.boxes[-1]
    h.source.fetch_error = "offline <b>bold?</b>"
    h.flow.check_now()
    pump()
    assert h.icons[-1] == QMessageBox.Warning and "offline <b>bold?</b>" in h.boxes[-1]
    count = len(h.boxes)
    h.clock.advance()
    h.c._automatic_check()  # automatic failures stay quiet
    pump()
    assert len(h.boxes) == count


def test_tray_check_when_turned_off_by_policy(tray):
    h = tray([rel("0.6.0")], policy=PolicyInfo(disabled=True), answer="OK")
    h.flow.check_now()
    assert h.boxes == ["Updates are managed by your organization."] and h.source.fetches == 0


def test_tray_shows_what_changed_after_an_update(tray):
    first = tray([rel("0.6.0", notes="Faster crop")], mode="ask", answer="Install now")
    first.c._automatic_check()
    pump()
    assert first.launched
    after = tray(current="0.6.0", answer="OK")
    after.flow.start()
    assert any("updated to 0.6.0" in m for m in after.messages)
    after.flow._tray.messageClicked.emit()
    assert after.boxes[-1] == "What's new in SnipForge 0.6.0"
    after.flow._tray.messageClicked.emit()
    assert after.boxes.count("What's new in SnipForge 0.6.0") == 1
