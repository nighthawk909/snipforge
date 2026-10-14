"""Update checks, download verification and the update prompt (network and installer faked)."""
import datetime as dt
import json

import pytest
from PySide6.QtNetwork import QNetworkReply
from PySide6.QtWidgets import QMessageBox

from snipforge import update_flow, updates
from snipforge.settings import app_settings

REPO_PAGE = "https://github.com/nighthawk909/snipforge/releases/tag/v9.0.0"
DL = "https://github.com/nighthawk909/snipforge/releases/download/v9.0.0/"


def release_json(**overrides):
    data = {
        "tag_name": "v9.0.0",
        "html_url": REPO_PAGE,
        "body": "### Features\n* better crop",
        "assets": [
            {"name": "SnipForge-9.0.0-setup.exe", "browser_download_url": DL + "SnipForge-9.0.0-setup.exe"},
            {"name": "SHA256SUMS.txt", "browser_download_url": DL + "SHA256SUMS.txt"},
            {"name": "sbom.cdx.json", "browser_download_url": DL + "sbom.cdx.json"},
        ],
    }
    data.update(overrides)
    return data


class FakeReply:
    def __init__(self, body=b"", error=QNetworkReply.NoError, url="https://github.com/x"):
        self.body, self.err, self._url = body, error, url

    def deleteLater(self):
        pass

    def error(self):
        return self.err

    def errorString(self):
        return "Operation canceled" if self.err == QNetworkReply.OperationCanceledError else "offline"

    def readAll(self):
        return self.body

    def url(self):
        from PySide6.QtCore import QUrl
        return QUrl(self._url)


# --- parsing and verification ------------------------------------------------------------
def test_parse_release_picks_matching_installer_and_sums():
    release = updates.parse_release(release_json())
    assert release.version == "9.0.0" and release.page == REPO_PAGE
    assert release.installer_name == "SnipForge-9.0.0-setup.exe"
    assert release.installer_url.endswith("setup.exe") and release.sums_url.endswith("SHA256SUMS.txt")
    assert "better crop" in release.notes


def test_parse_release_rejects_untrusted_urls_and_links():
    data = release_json(html_url="https://evil.example/page")
    data["assets"][0]["browser_download_url"] = "https://evil.example/SnipForge-9.0.0-setup.exe"
    release = updates.parse_release(data)
    assert release.page == updates.RELEASES_PAGE and release.installer_url is None
    with pytest.raises(ValueError):
        updates.parse_release(release_json(tag_name="nightly"))


def test_parse_release_trims_long_notes():
    release = updates.parse_release(release_json(body="line\n" * 1000))
    assert len(release.notes) <= updates.NOTES_MAX + 2


def test_expected_hash_and_trusted_hosts():
    digest = "a" * 64
    sums = f"{digest}  SnipForge-9.0.0-setup.exe\n{'b' * 64}  SnipForge-9.0.0-portable.zip\n"
    assert updates.expected_hash(sums, "SnipForge-9.0.0-setup.exe") == digest
    assert updates.expected_hash(sums, "missing.exe") is None
    assert updates.trusted_download("https://objects.githubusercontent.com/x")
    assert not updates.trusted_download("http://github.com/x")
    assert not updates.trusted_download("https://github.com.evil.example/x")


def test_install_mode():
    assert updates.install_mode(frozen=False) == "source"
    from pathlib import Path
    per_user = Path.home() / "AppData" / "Local" / "Programs" / "SnipForge" / "SnipForge.exe"
    assert updates.install_mode(str(per_user), frozen=True) == "per-user"
    assert updates.install_mode(r"C:\Program Files\SnipForge\SnipForge.exe", frozen=True) == "all-users"


def test_policy_turns_updates_off(monkeypatch):
    import winreg

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(winreg, "OpenKey", lambda hive, path: Key())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: (1, 4))
    assert updates.disabled_by_policy()


# --- checker -------------------------------------------------------------------------------
def test_checker_reports_newer_current_and_errors(qapp):
    checker = updates.UpdateChecker()
    results = []
    checker.finished.connect(lambda *a: results.append(a))
    checker._done(FakeReply(json.dumps(release_json()).encode()))
    checker._done(FakeReply(json.dumps(release_json(tag_name="v0.0.1")).encode()))
    checker._done(FakeReply(b"not json"))
    checker._done(FakeReply(error=QNetworkReply.HostNotFoundError))
    assert [r[0] for r in results] == ["newer", "current", "error", "error"]
    assert results[0][1].version == "9.0.0"


# --- downloader ----------------------------------------------------------------------------
def make_downloader(tmp_path, installer_bytes, sums_text):
    dl = updates.UpdateDownloader(tmp_path)
    dl._release = updates.parse_release(release_json())
    dl._sums = sums_text
    return dl


def test_download_verifies_checksum(qapp, tmp_path):
    data = b"installer bytes"
    good = f"{updates.sha256_bytes(data)}  SnipForge-9.0.0-setup.exe\n"
    path, error = make_downloader(tmp_path, data, good).verify_and_save(data)
    assert path is not None and path.read_bytes() == data and error == ""
    path, error = make_downloader(tmp_path, data, good).verify_and_save(b"tampered")
    assert path is None and "checksum" in error
    path, error = make_downloader(tmp_path, data, "").verify_and_save(data)
    assert path is None and "no checksum" in error


def test_download_flow_with_fake_network(qapp, tmp_path):
    data = b"installer bytes"
    sums = f"{updates.sha256_bytes(data)}  SnipForge-9.0.0-setup.exe\n".encode()
    dl = updates.UpdateDownloader(tmp_path)
    results = []
    dl.finished.connect(lambda *a: results.append(a))
    requests = []
    dl._get = lambda url, handler: requests.append((url, handler))
    dl.start(updates.parse_release(release_json()))
    url, handler = requests.pop()
    assert url.endswith("SHA256SUMS.txt")
    handler(FakeReply(sums, url="https://objects.githubusercontent.com/sums"))
    url, handler = requests.pop()
    assert url.endswith("setup.exe")
    handler(FakeReply(data, url="https://objects.githubusercontent.com/setup"))
    assert results[0][0] is not None and results[0][0].read_bytes() == data


def test_download_refuses_unexpected_redirect(qapp, tmp_path):
    dl = updates.UpdateDownloader(tmp_path)
    results = []
    dl.finished.connect(lambda *a: results.append(a))
    dl._release = updates.parse_release(release_json())
    dl._got_sums(FakeReply(b"x", url="https://evil.example/sums"))
    assert results[0][0] is None and "unexpected" in results[0][1]


def test_download_without_installer_asset(qapp, tmp_path):
    dl = updates.UpdateDownloader(tmp_path)
    results = []
    dl.finished.connect(lambda *a: results.append(a))
    dl.start(updates.parse_release(release_json(assets=[])))
    assert results[0][0] is None


# --- the prompt and scheduling -------------------------------------------------------------
def test_check_is_due():
    now = dt.datetime(2026, 10, 14, 12, 0)
    assert update_flow.check_is_due(now, None)
    assert update_flow.check_is_due(now, "garbage")
    assert not update_flow.check_is_due(now, "2026-10-14T08:00:00")
    assert update_flow.check_is_due(now, "2026-10-13T08:00:00")


@pytest.fixture
def flow(qapp, monkeypatch):
    from PySide6.QtWidgets import QSystemTrayIcon
    monkeypatch.setattr(updates, "disabled_by_policy", lambda: False)
    shown = []
    for name in ("information", "warning"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, n=name: shown.append(n))
    f = update_flow.UpdateFlow(QSystemTrayIcon())
    f.shown = shown
    monkeypatch.setattr(f._checker, "check", lambda: None)
    for key in (update_flow.SKIP_KEY, update_flow.LAST_CHECK_KEY, update_flow.AUTO_KEY):
        app_settings().remove(key)
    return f


def choose(monkeypatch, label):
    """Answer the update prompt by clicking the button with this label."""
    def exec_(box):
        box._clicked = next(b for b in box.buttons() if b.text() == label)
        return 0

    monkeypatch.setattr(QMessageBox, "exec", exec_)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: box._clicked)


def test_per_user_install_downloads_and_installs(flow, monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "install_mode", lambda: "per-user")
    started = []
    monkeypatch.setattr(update_flow.UpdateFlow, "download", lambda self, r: started.append(r.version))
    choose(monkeypatch, "Update now")
    flow._manual = False
    flow._checked("newer", updates.parse_release(release_json()))
    assert started == ["9.0.0"]


def test_skip_this_version_is_remembered(flow, monkeypatch):
    monkeypatch.setattr(updates, "install_mode", lambda: "per-user")
    choose(monkeypatch, "Skip this version")
    flow._manual = False
    flow._checked("newer", updates.parse_release(release_json()))
    assert app_settings().value(update_flow.SKIP_KEY) == "9.0.0"
    offered = []
    monkeypatch.setattr(update_flow.UpdateFlow, "offer", lambda self, r: offered.append(r))
    flow._checked("newer", updates.parse_release(release_json()))
    assert offered == []  # automatic checks stay quiet about a skipped version
    flow._manual = True
    flow._checked("newer", updates.parse_release(release_json()))
    assert len(offered) == 1  # but asking explicitly still offers it


def test_managed_install_opens_download_page(flow, monkeypatch):
    monkeypatch.setattr(updates, "install_mode", lambda: "all-users")
    opened = []
    from PySide6.QtGui import QDesktopServices
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    choose(monkeypatch, "Open download page")
    flow.offer(updates.parse_release(release_json()))
    assert opened == [REPO_PAGE]


def test_manual_check_messages(flow):
    flow._manual = True
    flow._checked("current", updates.parse_release(release_json(tag_name="v0.0.1")))
    flow._checked("error", "offline")
    assert flow.shown == ["information", "information"]
    flow._manual = False
    flow._checked("error", "offline")  # automatic failures stay silent
    assert flow.shown == ["information", "information"]


def test_automatic_check_respects_setting_and_schedule(flow, monkeypatch):
    checks = []
    monkeypatch.setattr(update_flow.UpdateFlow, "_check", lambda self, manual: checks.append(manual))
    update_flow.set_automatic(False)
    flow._automatic_check()
    update_flow.set_automatic(True)
    flow._automatic_check()
    app_settings().setValue(update_flow.LAST_CHECK_KEY, dt.datetime.now().isoformat())
    flow._automatic_check()
    assert checks == [False]


def test_failed_download_reports_and_install_runs_installer(flow, monkeypatch, tmp_path):
    flow._downloaded(None, "The download didn't match its checksum.")
    assert flow.shown == ["warning"]
    flow._downloaded(None, "Operation canceled")
    assert flow.shown == ["warning"]  # cancelling isn't an error
    launched, quit_calls = [], []
    monkeypatch.setattr(update_flow.subprocess, "Popen", lambda args, **k: launched.append(args))
    monkeypatch.setattr(update_flow.QTimer, "singleShot", lambda ms, fn: quit_calls.append(ms))
    installer = tmp_path / "SnipForge-9.0.0-setup.exe"
    installer.write_bytes(b"x")
    flow.install(installer)
    assert launched[0][0] == str(installer) and "/VERYSILENT" in launched[0] and "/CURRENTUSER" in launched[0]
    assert quit_calls
