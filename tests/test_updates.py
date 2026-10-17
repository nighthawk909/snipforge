"""Update engine building blocks: versions, release lists, prefs, policy, sources and transports.

Network, registry and installer launches are faked; the folder feed also runs once against a real
folder on disk through the threaded file transport.
"""
import datetime as dt
import json
import os
import time
from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtNetwork import QNetworkReply

from snipforge import (
    project,
    update_flow,
    update_platform,
    update_policy,
    update_prefs,
    update_sources,
    update_transport,
    updates,
)
from snipforge.settings import app_settings
from snipforge.update_policy import PolicyInfo, feed_location, parse_policy
from snipforge.update_prefs import UpdatePrefs, load_prefs, save_prefs

API = f"{project.API_URL}/repos/{project.REPO}/releases?per_page=20"
# A repository on a GitHub Enterprise Server, for the tests that move SnipForge off github.com.
GHE = {"repo": "fixture-org/snipforge", "server_url": "https://ghe.example.test"}
GHE_RELEASES = "https://ghe.example.test/fixture-org/snipforge/releases"
GHE_API = "https://ghe.example.test/api/v3/repos/fixture-org/snipforge/releases?per_page=20"
UPDATE_KEYS = ("auto_update", "update_mode", "update_channel", "update_pinned_version", "skip_version",
               "last_update_check", "update_ready", "update_installed")


def dl(version, name):
    return f"{project.RELEASES_URL}/download/v{version}/{name}"


def release_json(version="9.0.0", **overrides):
    setup = f"SnipForge-{version}-setup.exe"
    data = {
        "tag_name": f"v{version}",
        "html_url": f"{project.RELEASES_URL}/tag/v{version}",
        "body": "### Features\n* better crop",
        "prerelease": False,
        "draft": False,
        "published_at": "2026-10-14T12:53:34Z",
        "assets": [
            {"name": setup, "browser_download_url": dl(version, setup)},
            {"name": "SHA256SUMS.txt", "browser_download_url": dl(version, "SHA256SUMS.txt")},
            {"name": "sbom.cdx.json", "browser_download_url": dl(version, "sbom.cdx.json")},
        ],
    }
    data.update(overrides)
    return data


def spin(until, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if until():
            return True
        time.sleep(0.005)
    return until()


class Stub:
    def cancel(self):
        pass


class FakeTransport:
    """get() answers from a dict: location -> bytes | (bytes, final location) | Error(message)."""

    class Error(str):
        pass

    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.requests = []

    def get(self, location, *, timeout_ms, max_bytes, done, progress=None, accept=None):
        self.requests.append(location)
        answer = self.responses.get(location, FakeTransport.Error("not found"))
        if isinstance(answer, FakeTransport.Error):
            done(None, str(answer), location)
        else:
            data, final = answer if isinstance(answer, tuple) else (answer, location)
            if progress:
                progress(len(data), len(data))
            done(data, "", final)
        return Stub()


@pytest.fixture
def clean_settings():
    def wipe():
        settings = app_settings()
        for key in UPDATE_KEYS:
            settings.remove(key)
        settings.sync()

    wipe()
    yield app_settings()
    wipe()


def collect():
    results = []
    return results, lambda *args: results.append(args)


# --- versions -----------------------------------------------------------------------------
def test_versions_normalize_and_order_including_prereleases():
    assert updates.normalize_version("v0.6.0-beta.1") == "0.6.0-beta.1"
    assert updates.normalize_version(" 1.2.3 ") == "1.2.3"
    assert updates.normalize_version("nightly") is None and updates.normalize_version("") is None
    ordered = ["0.5.0", "0.6.0-alpha.1", "0.6.0-beta.1", "0.6.0-beta.2", "0.6.0-rc.1", "0.6.0", "0.6.1", "0.10.0"]
    keys = [updates.version_key(v) for v in ordered]
    assert keys == sorted(keys)
    assert updates.compare_versions("0.6", "0.6.0") == 0
    assert updates.compare_versions("v0.6.0", "0.5.9") == 1
    assert updates.is_newer("v1.0.0", "0.5.0") and not updates.is_newer("nightly", "0.5.0")
    with pytest.raises(ValueError):
        updates.compare_versions("nightly", "0.5.0")


def test_preview_build_versions_sort_below_the_release_and_by_number():
    """Preview builds are always X.Y.Z-preview.N (scripts/publish_feed.py, .github/workflows/preview.yml)."""
    ordered = ["0.5.0", "0.6.0-preview.1", "0.6.0-preview.2", "0.6.0-preview.9", "0.6.0-preview.10", "0.6.0"]
    keys = [updates.version_key(v) for v in ordered]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    assert updates.normalize_version("v0.6.0-preview.10") == "0.6.0-preview.10"
    assert updates.is_newer("0.6.0", "0.6.0-preview.3") and not updates.is_newer("0.6.0-preview.3", "0.6.0")


# --- GitHub release data ------------------------------------------------------------------
def test_parse_release_picks_matching_installer_and_sums():
    release = updates.parse_release(release_json())
    assert release.version == "9.0.0" and release.page.endswith("/tag/v9.0.0")
    assert release.installer_name == "SnipForge-9.0.0-setup.exe"
    assert release.installer_url.endswith("setup.exe") and release.sums_url.endswith("SHA256SUMS.txt")
    assert "better crop" in release.notes
    assert release.prerelease is False and release.channel == "stable"
    assert release.published == dt.datetime(2026, 10, 14, 12, 53, 34, tzinfo=dt.UTC)
    assert release.sha256 is None


def test_parse_release_rejects_untrusted_urls_and_links():
    data = release_json(html_url="https://evil.example/page")
    data["assets"][0]["browser_download_url"] = "https://evil.example/SnipForge-9.0.0-setup.exe"
    other_repo = f"{project.SERVER_URL}/someone-else/repo"
    data["assets"][1]["browser_download_url"] = f"{other_repo}/releases/download/v9/SHA256SUMS.txt"
    release = updates.parse_release(data)
    assert release.page == updates.RELEASES_PAGE and release.installer_url is None and release.sums_url is None
    with pytest.raises(ValueError):
        updates.parse_release(release_json(tag_name="nightly"))


def test_parse_release_trims_long_notes():
    release = updates.parse_release(release_json(body="line\n" * 1000))
    assert len(release.notes) <= updates.NOTES_MAX + 2


def test_parse_release_handles_prerelease_installer_names():
    release = updates.parse_release(release_json("0.6.0-beta.1", prerelease=True))
    assert release.version == "0.6.0-beta.1" and release.prerelease and release.channel == "preview"
    assert release.installer_name == "SnipForge-0.6.0-beta.1-setup.exe"
    other = release_json("0.6.0")
    other["assets"][0]["name"] = "SnipForge-0.7.0-setup.exe"  # an installer for another version is ignored
    assert updates.parse_release(other).installer_url is None
    preview = updates.parse_release(release_json("0.6.0-preview.10", prerelease=True))
    assert preview.version == "0.6.0-preview.10" and preview.installer_name == "SnipForge-0.6.0-preview.10-setup.exe"


def test_versionless_installer_copy_is_not_the_update():
    """Stable releases also carry SnipForge-setup.exe for the permanent download link; updates use the named one."""
    data = release_json("0.6.0")
    copy = {"name": "SnipForge-setup.exe", "browser_download_url": dl("0.6.0", "SnipForge-setup.exe")}
    data["assets"].insert(0, copy)
    release = updates.parse_release(data)
    assert release.installer_name == "SnipForge-0.6.0-setup.exe" and release.installer_url.endswith("0.6.0-setup.exe")


def test_parse_releases_skips_drafts_and_bad_tags_newest_first():
    data = [release_json("0.4.0"), release_json("0.7.0", draft=True), release_json(tag_name="nightly"),
            release_json("0.6.0-beta.1", prerelease=True), release_json("0.5.0"), "junk"]
    assert [r.version for r in updates.parse_releases(data)] == ["0.6.0-beta.1", "0.5.0", "0.4.0"]
    with pytest.raises(ValueError):
        updates.parse_releases({"message": "API rate limit exceeded"})


def test_expected_hash_and_trusted_hosts():
    digest = "a" * 64
    sums = f"{digest}  SnipForge-9.0.0-setup.exe\n{'b' * 64}  SnipForge-9.0.0-portable.zip\n"
    assert updates.expected_hash(sums, "SnipForge-9.0.0-setup.exe") == digest
    windows_lines = sums.replace("\n", "\r\n")  # SHA256SUMS.txt is written on Windows
    assert updates.expected_hash(windows_lines, "SnipForge-9.0.0-setup.exe") == digest
    assert updates.expected_hash(sums, "missing.exe") is None
    github = project.download_hosts("https://github.com")
    assert updates.trusted_download("https://objects.githubusercontent.com/x", github)
    assert not updates.trusted_download("http://github.com/x", github)
    assert not updates.trusted_download("https://github.com.evil.example/x", github)
    assert updates.trusted_download(f"{project.REPO_URL}/x")  # the configured server's hosts by default
    assert not updates.trusted_download("https://objects.githubusercontent.com/x", {"ghe.example.test"})


def test_release_round_trips_through_settings_dict():
    release = updates.parse_release(release_json("0.6.0-beta.1", prerelease=True))
    assert updates.release_from_dict(json.loads(json.dumps(updates.release_to_dict(release)))) == release
    assert updates.release_from_dict({"version": "nightly"}) is None
    assert updates.release_from_dict("garbage") is None


# --- choosing what to offer ---------------------------------------------------------------
def releases(*versions):
    return [updates.Release(v, "", "", f"SnipForge-{v}-setup.exe", dl(v, f"SnipForge-{v}-setup.exe"),
                            dl(v, "SHA256SUMS.txt"), prerelease="-" in v) for v in versions]


def test_channel_filtering():
    all_ = releases("0.7.0-beta.1", "0.6.0", "0.5.0")
    assert [r.version for r in updates.filter_channel(all_, "stable")] == ["0.6.0", "0.5.0"]
    assert [r.version for r in updates.filter_channel(all_, "preview")] == ["0.7.0-beta.1", "0.6.0", "0.5.0"]


def test_pick_update_offers_newest_in_channel_up_to_the_pin():
    all_ = releases("0.7.0", "0.6.1-beta.1", "0.6.0", "0.5.0")
    pick = updates.pick_update
    assert pick(all_, "0.5.0", UpdatePrefs()).version == "0.7.0"
    assert pick(all_, "0.5.0", UpdatePrefs(pinned_version="0.6.0")).version == "0.6.0"
    assert pick(all_, "0.5.0", UpdatePrefs(pinned_version="0.6.5", channel="preview")).version == "0.6.1-beta.1"
    assert pick(all_, "0.5.0", UpdatePrefs(pinned_version="0.6.5")).version == "0.6.0"
    assert pick(all_, "0.5.0", UpdatePrefs(pinned_version="0.5.0")) is None
    assert pick(all_, "0.7.0", UpdatePrefs()) is None
    assert pick(all_, "0.6.1-beta.1", UpdatePrefs()).version == "0.7.0"


def test_skipping_a_version_also_skips_older_ones():
    prefs = UpdatePrefs(skip_version="0.6.0")
    r070, r060, r051 = releases("0.7.0", "0.6.0", "0.5.1")
    assert updates.is_skipped(r060, prefs) and updates.is_skipped(r051, prefs)
    assert not updates.is_skipped(r070, prefs) and not updates.is_skipped(r060, UpdatePrefs())


# --- preferences --------------------------------------------------------------------------
def test_prefs_defaults_round_trip_and_validation(clean_settings):
    assert load_prefs() == UpdatePrefs(mode="ask", channel="stable", pinned_version=None, skip_version=None)
    prefs = UpdatePrefs(mode="auto", channel="preview", pinned_version="v0.5.0", skip_version="0.6.0")
    assert prefs.pinned_version == "0.5.0"  # normalized
    save_prefs(prefs)
    assert load_prefs() == prefs
    save_prefs(replace(prefs, pinned_version=None, skip_version=None))
    assert load_prefs().pinned_version is None and not clean_settings.contains("update_pinned_version")
    for bad in ({"mode": "sometimes"}, {"channel": "beta"}, {"pinned_version": "nightly"}, {"skip_version": "x"}):
        with pytest.raises(ValueError):
            UpdatePrefs(**bad)


def test_prefs_migrate_the_old_auto_update_setting(clean_settings):
    clean_settings.setValue("auto_update", True)
    assert load_prefs().mode == "ask"
    assert clean_settings.value("update_mode") == "ask"  # migrated once, then the new key wins
    clean_settings.remove("update_mode")
    clean_settings.setValue("auto_update", False)
    assert load_prefs().mode == "manual"
    clean_settings.setValue("auto_update", True)
    assert load_prefs().mode == "manual"


def test_prefs_ignore_corrupt_values(clean_settings):
    clean_settings.setValue("update_mode", "banana")
    clean_settings.setValue("update_channel", 5)
    clean_settings.setValue("update_pinned_version", "zzz")
    clean_settings.setValue("skip_version", "")
    assert load_prefs() == UpdatePrefs()


def test_saving_prefs_keeps_the_old_setting_meaningful_for_older_versions(clean_settings):
    for prefs, legacy in ((UpdatePrefs(mode="manual"), False), (UpdatePrefs(mode="ask"), True),
                          (UpdatePrefs(mode="auto", pinned_version="0.5.0"), False)):
        save_prefs(prefs)
        assert clean_settings.value("auto_update", None, bool) is legacy


def test_last_checked_round_trip(clean_settings):
    assert update_prefs.load_last_checked() is None
    when = dt.datetime(2026, 10, 16, 9, 30)
    update_prefs.save_last_checked(when)
    assert update_prefs.load_last_checked() == when
    clean_settings.setValue("last_update_check", "garbage")
    assert update_prefs.load_last_checked() is None


def test_tray_toggle_still_works(clean_settings, monkeypatch):
    monkeypatch.setattr(update_policy, "read_policy", lambda: PolicyInfo())
    update_flow.set_automatic(False)
    assert load_prefs().mode == "manual" and not update_flow.automatic_enabled()
    update_flow.set_automatic(True)
    assert load_prefs().mode == "ask" and update_flow.automatic_enabled()
    save_prefs(UpdatePrefs(mode="auto"))
    update_flow.set_automatic(True)
    assert load_prefs().mode == "auto"
    monkeypatch.setattr(update_policy, "read_policy", lambda: PolicyInfo(disabled=True))
    assert not update_flow.automatic_enabled()
    monkeypatch.setattr(update_policy, "read_policy", lambda: PolicyInfo(mode="manual"))
    assert not update_flow.automatic_enabled()


# --- policy -------------------------------------------------------------------------------
def test_policy_defaults_when_nothing_is_set():
    policy = parse_policy([{}, {}])
    assert policy == PolicyInfo() and not policy.managed and policy.source == "github" and policy.allow_rollback


def test_policy_most_restrictive_wins_for_switches():
    assert parse_policy([{"DisableUpdateCheck": 0}, {"DisableUpdateCheck": 1}]).disabled
    assert parse_policy([{"DisableUpdateCheck": "1"}, {}]).disabled
    assert not parse_policy([{"DisableUpdateCheck": 0}, {}]).disabled
    policy = parse_policy([{"AllowRollback": 1}, {"AllowRollback": 0}])
    assert not policy.allow_rollback and policy.managed


def test_policy_machine_values_win_over_user_values():
    policy = parse_policy([{"UpdateMode": "Auto"}, {"UpdateMode": "manual", "UpdateChannel": "PREVIEW"}])
    assert policy.mode == "auto" and policy.channel == "preview" and policy.managed
    bad = parse_policy([{"UpdateMode": "sometimes", "UpdateChannel": 3}, {}])
    assert bad.mode is None and bad.channel is None and len(bad.problems) == 2


def test_policy_forces_mode_and_channel_over_user_prefs():
    prefs = UpdatePrefs(mode="auto", channel="preview", pinned_version="0.5.0")
    forced = PolicyInfo(mode="manual", channel="stable").apply(prefs)
    assert forced == UpdatePrefs(mode="manual", channel="stable", pinned_version="0.5.0")
    assert PolicyInfo().apply(prefs) == prefs


def test_feed_locations_are_validated(monkeypatch):
    monkeypatch.setenv("SNIPFORGE_TEST_ROOT", r"C:\Feeds")
    assert feed_location("https://it.example.com/snipforge/") == "https://it.example.com/snipforge/"
    assert feed_location(r"\\fileserver\apps\SnipForge") == r"\\fileserver\apps\SnipForge"
    assert feed_location(r'"C:\Users\me\OneDrive - Contoso\SnipForge"') == r"C:\Users\me\OneDrive - Contoso\SnipForge"
    assert feed_location(r"%SNIPFORGE_TEST_ROOT%\SnipForge") == r"C:\Feeds\SnipForge"
    for bad in ("http://it.example.com/feed", "ftp://x/y", "file:///C:/feed", r"relative\feed", r"C:feed",
                "https://user:secret@it.example.com/feed", "https://", "", r"%NOT_A_REAL_VARIABLE_42%\feed"):
        assert feed_location(bad) is None, bad


def test_policy_feed_source():
    feed = parse_policy([{"UpdateFeed": r"\\fileserver\apps\SnipForge"}, {}])
    assert feed.source == "feed" and feed.feed == r"\\fileserver\apps\SnipForge" and feed.managed
    assert feed.source_problem == ""
    missing = parse_policy([{"UpdateSource": "feed"}, {}])
    assert missing.source == "feed" and missing.feed is None and missing.source_problem
    invalid = parse_policy([{}, {"UpdateSource": "Feed", "UpdateFeed": "http://insecure.example/feed"}])
    assert invalid.source == "feed" and invalid.feed is None and "https" in invalid.source_problem
    github = parse_policy([{"UpdateSource": "github", "UpdateFeed": r"\\fileserver\apps\SnipForge"}, {}])
    assert github.source == "github" and github.feed is None


def test_user_policy_cant_undo_a_machine_feed():
    machine_feed = parse_policy([{"UpdateFeed": r"\\fileserver\apps\SnipForge"}, {"UpdateSource": "github"}])
    assert machine_feed.source == "feed" and machine_feed.feed == r"\\fileserver\apps\SnipForge"
    machine_github = parse_policy([{"UpdateSource": "github"}, {"UpdateFeed": r"\\fileserver\apps\SnipForge"}])
    assert machine_github.source == "github" and machine_github.feed is None
    split = parse_policy([{"UpdateSource": "feed"}, {"UpdateFeed": r"\\fileserver\apps\SnipForge"}])
    assert split.source == "feed" and split.feed == r"\\fileserver\apps\SnipForge"


def test_policy_is_read_from_both_registry_hives(monkeypatch):
    import winreg

    values = {winreg.HKEY_LOCAL_MACHINE: {"UpdateMode": "manual"},
              winreg.HKEY_CURRENT_USER: {"DisableUpdateCheck": 1, "UpdateMode": "auto"}}

    class Key:
        def __init__(self, hive):
            self.hive = hive

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def query(key, name):
        if name not in values[key.hive]:
            raise FileNotFoundError(name)
        return values[key.hive][name], winreg.REG_SZ

    monkeypatch.setattr(winreg, "OpenKey", lambda hive, path: Key(hive))
    monkeypatch.setattr(winreg, "QueryValueEx", query)
    hives = update_platform.read_policy_hives()
    assert hives == [{"UpdateMode": "manual"}, {"DisableUpdateCheck": 1, "UpdateMode": "auto"}]
    policy = update_policy.read_policy()
    assert policy.disabled and policy.mode == "manual"
    assert updates.disabled_by_policy()


# --- manifest (folder feed) ---------------------------------------------------------------
SHA_A, SHA_B, SHA_C = "a" * 64, "B" * 64, "c" * 64


def manifest(*entries, schema=1):
    return {"schema": schema, "releases": list(entries)}


def entry(version, channel="stable", assets=None, **extra):
    data = {"version": version, "channel": channel, "published": "2026-10-20T15:00:00Z", "notes": f"Notes {version}",
            "assets": assets if assets is not None else
            [{"platform": "windows", "name": f"SnipForge-{version}-setup.exe", "sha256": SHA_A}]}
    data.update(extra)
    return data


def test_parse_manifest_valid():
    data = manifest(
        entry("0.6.0"),
        entry("0.7.0-beta.1", "preview", assets=[
            {"platform": "macos", "name": "SnipForge-0.7.0-beta.1.dmg", "sha256": SHA_C},
            {"platform": "windows", "name": "SnipForge-0.7.0-beta.1-setup.exe", "sha256": SHA_B}]),
        entry("0.6.5-mac", assets=[{"platform": "macos", "name": "SnipForge-0.6.5.dmg", "sha256": SHA_C}]),
    )
    found = update_sources.parse_manifest(json.dumps(data).encode(), r"\\srv\apps\SnipForge")
    assert [r.version for r in found] == ["0.7.0-beta.1", "0.6.0"]  # newest first; mac-only release left out
    beta, stable = found
    assert beta.prerelease and beta.sha256 == "b" * 64 and beta.installer_name == "SnipForge-0.7.0-beta.1-setup.exe"
    assert beta.installer_url == r"\\srv\apps\SnipForge\SnipForge-0.7.0-beta.1-setup.exe"
    assert stable.published == dt.datetime(2026, 10, 20, 15, 0, tzinfo=dt.UTC) and stable.notes == "Notes 0.6.0"
    assert stable.sums_url is None and stable.page == ""
    mac = update_sources.parse_manifest(data, "https://it.example.com/snipforge/", platform="macos")
    assert [r.installer_url for r in mac] == ["https://it.example.com/snipforge/SnipForge-0.7.0-beta.1.dmg",
                                              "https://it.example.com/snipforge/SnipForge-0.6.5.dmg"]


def test_parse_manifest_tolerates_bom_and_missing_optional_fields():
    data = manifest({"version": "0.6.0", "channel": "stable",
                     "assets": [{"platform": "windows", "name": "SnipForge-0.6.0-setup.exe", "sha256": SHA_A}]},
                    entry("0.5.0", published="last tuesday", notes=None))
    found = update_sources.parse_manifest(b"\xef\xbb\xbf" + json.dumps(data).encode(), r"C:\feed")
    assert [(r.version, r.published, r.notes) for r in found] == [("0.6.0", None, ""), ("0.5.0", None, "")]


def test_parse_manifest_takes_the_channel_from_the_field():
    """publish_feed.py can put a stable-looking version on the preview channel before promoting it."""
    found = update_sources.parse_manifest(manifest(entry("0.6.0", "preview"), entry("0.6.0-preview.2", "preview")),
                                          r"C:\feed")
    assert [(r.version, r.channel) for r in found] == [("0.6.0", "preview"), ("0.6.0-preview.2", "preview")]
    assert updates.filter_channel(found, "stable") == []


@pytest.mark.parametrize("raw", [
    b"not json", b"[]", b'{"releases": []}', b'{"schema": "1", "releases": []}', b'{"schema": true, "releases": []}',
    b'{"schema": 2, "releases": []}', b'{"schema": 1, "releases": {}}',
    json.dumps(manifest(entry("nightly"), entry("0.6.0", channel="beta"))).encode(),
])
def test_parse_manifest_rejects_malformed_files(raw):
    with pytest.raises(ValueError):
        update_sources.parse_manifest(raw, r"C:\feed")


def test_parse_manifest_skips_bad_entries_but_keeps_good_ones():
    data = manifest(
        entry("0.6.0"),
        entry("0.6.1", assets=[{"platform": "windows", "name": r"..\evil-setup.exe", "sha256": SHA_A}]),
        entry("0.6.2", assets=[{"platform": "windows", "name": "SnipForge-0.6.2-setup.exe", "sha256": "abc"}]),
        entry("0.6.3", assets=[{"platform": "windows", "name": "SnipForge-0.6.3.msi", "sha256": SHA_A}]),
        entry("0.6.4", channel=None),
        entry("0.6.0"),  # duplicate
        "junk",
    )
    assert [r.version for r in update_sources.parse_manifest(data, r"C:\feed")] == ["0.6.0"]
    assert update_sources.parse_manifest(manifest(), r"C:\feed") == []  # an empty feed is fine


# --- sources ------------------------------------------------------------------------------
def test_github_source_fetches_the_release_list():
    transport = FakeTransport({API: json.dumps([release_json("0.6.0"), release_json("0.5.0")]).encode()})
    results, done = collect()
    update_sources.GitHubSource(transport=transport).fetch(done)
    assert transport.requests == [API]
    found, error = results[0]
    assert error == "" and [r.version for r in found] == ["0.6.0", "0.5.0"]


def test_github_source_refuses_a_list_from_another_host():
    redirected = (json.dumps([release_json("0.6.0")]).encode(), "https://evil.example/releases")
    results, done = collect()
    update_sources.GitHubSource(transport=FakeTransport({API: redirected})).fetch(done)
    assert results[0][0] is None and "UpdateFeed" in results[0][1]


def test_github_source_reports_bad_responses_and_network_errors():
    for answer in (b"not json", json.dumps({"message": "rate limited"}).encode(), FakeTransport.Error("offline")):
        results, done = collect()
        update_sources.GitHubSource(transport=FakeTransport({API: answer})).fetch(done)
        assert results[0][0] is None and results[0][1]
    with pytest.raises(ValueError):
        update_sources.GitHubSource(api_base="http://api.github.com")


def github_download(tmp_path, data, sums, sums_final=None, installer_final=None, source=None, release=None):
    release = release or updates.parse_release(release_json())
    sums_answer = (sums, sums_final) if sums_final else sums
    installer_answer = (data, installer_final) if installer_final else data
    transport = FakeTransport({release.sums_url: sums_answer, release.installer_url: installer_answer})
    results, done = collect()
    progress = []
    source = source or update_sources.GitHubSource
    source(transport=transport).download(release, tmp_path, done, lambda *a: progress.append(a))
    path, error, digest = results[0]
    assert (digest == updates.sha256_bytes(data)) if path else digest == ""
    return (path, error), progress


def test_github_download_verifies_checksum_and_saves(tmp_path):
    data = b"installer bytes"
    sums = f"{updates.sha256_bytes(data)}  SnipForge-9.0.0-setup.exe\n".encode()
    (path, error), progress = github_download(tmp_path, data, sums)
    assert error == "" and path == tmp_path / "SnipForge-9.0.0-setup.exe" and path.read_bytes() == data
    assert progress and not list(tmp_path.glob("*.part"))


def test_github_com_downloads_may_redirect_to_its_file_storage(tmp_path):
    public = {"repo": "owner/snipforge", "server_url": "https://github.com"}
    data = release_json()
    for asset in data["assets"]:
        asset["browser_download_url"] = asset["browser_download_url"].replace(
            project.RELEASES_URL, "https://github.com/owner/snipforge/releases")
    release = updates.parse_release(data, public["repo"], server_url=public["server_url"])
    payload = b"installer bytes"
    sums = f"{updates.sha256_bytes(payload)}  SnipForge-9.0.0-setup.exe\n".encode()
    (path, error), _ = github_download(tmp_path, payload, sums, "https://objects.githubusercontent.com/s",
                                       "https://release-assets.githubusercontent.com/i",
                                       source=lambda **kw: update_sources.GitHubSource(**public, **kw),
                                       release=release)
    assert error == "" and path.read_bytes() == payload


def ghe_release_json(version="9.0.0"):
    data = release_json(version, html_url=f"{GHE_RELEASES}/tag/v{version}")
    for asset in data["assets"]:
        asset["browser_download_url"] = asset["browser_download_url"].replace(project.RELEASES_URL, GHE_RELEASES)
    return data


def test_github_source_reads_releases_from_an_enterprise_server(tmp_path):
    transport = FakeTransport({GHE_API: json.dumps([ghe_release_json("0.6.0")]).encode()})
    results, done = collect()
    source = update_sources.GitHubSource(**GHE, transport=transport)
    source.fetch(done)
    assert transport.requests == [GHE_API]
    release = results[0][0][0]
    assert release.page == f"{GHE_RELEASES}/tag/v0.6.0"
    assert release.installer_url == f"{GHE_RELEASES}/download/v0.6.0/SnipForge-0.6.0-setup.exe"
    payload = b"enterprise installer"
    transport.responses[release.sums_url] = f"{updates.sha256_bytes(payload)}  SnipForge-0.6.0-setup.exe\n".encode()
    transport.responses[release.installer_url] = payload
    downloads, done = collect()
    source.download(release, tmp_path, done)
    assert downloads[0][1] == "" and downloads[0][0].read_bytes() == payload


def test_enterprise_downloads_refuse_github_com_storage(tmp_path):
    """Only github.com hands downloads to its file storage; an enterprise copy's must stay on its server."""
    release = updates.parse_release(ghe_release_json(), GHE["repo"], server_url=GHE["server_url"])
    payload = b"x"
    sums = f"{updates.sha256_bytes(payload)}  SnipForge-9.0.0-setup.exe\n".encode()
    transport = FakeTransport({release.sums_url: (sums, "https://objects.githubusercontent.com/s"),
                               release.installer_url: payload})
    results, done = collect()
    update_sources.GitHubSource(**GHE, transport=transport).download(release, tmp_path, done)
    assert results[0][0] is None and "unexpected" in results[0][1] and not any(tmp_path.iterdir())


def test_enterprise_releases_ignore_links_to_other_servers():
    release = updates.parse_release(release_json(), GHE["repo"], server_url=GHE["server_url"])
    assert release.page == f"{GHE_RELEASES}/latest" and release.installer_url is None and release.sums_url is None


@pytest.mark.parametrize("answer", [
    FakeTransport.Error(update_transport.NEEDS_SIGN_IN),
    FakeTransport.Error(update_transport.NOT_FOUND),
    (b"<html>Sign in to your account</html>", "https://login.example.com/sso"),
], ids=["sign-in", "not-found", "sign-in-page"])
def test_github_source_explains_a_repository_it_cant_see(answer):
    """A private or internal repository on GitHub Enterprise needs a sign-in the updater doesn't do,
    so the message points at the organization's own update location instead."""
    results, done = collect()
    update_sources.GitHubSource(**GHE, transport=FakeTransport({GHE_API: answer})).fetch(done)
    found, error = results[0]
    assert found is None
    assert GHE_RELEASES in error and "sign-in" in error
    assert "IT team" in error and "UpdateFeed" in error


def test_github_source_keeps_other_errors_as_they_are():
    results, done = collect()
    update_sources.GitHubSource(**GHE, transport=FakeTransport({GHE_API: FakeTransport.Error("offline")})).fetch(done)
    assert results[0] == (None, "offline")


def test_github_download_rejects_checksum_mismatch_and_missing_checksum(tmp_path):
    sums = f"{'0' * 64}  SnipForge-9.0.0-setup.exe\n".encode()
    (path, error), _ = github_download(tmp_path, b"tampered", sums)
    assert path is None and "checksum" in error and not any(tmp_path.iterdir())
    (path, error), _ = github_download(tmp_path, b"x", b"")
    assert path is None and "no checksum" in error


def test_github_download_refuses_unexpected_redirects(tmp_path):
    data = b"x"
    sums = f"{updates.sha256_bytes(data)}  SnipForge-9.0.0-setup.exe\n".encode()
    (path, error), _ = github_download(tmp_path, data, sums, sums_final="https://evil.example/sums")
    assert path is None and "unexpected" in error
    (path, error), _ = github_download(tmp_path, data, sums, installer_final="https://evil.example/setup.exe")
    assert path is None and "unexpected" in error


def test_github_download_needs_installer_and_checksum_assets(tmp_path):
    results, done = collect()
    release = updates.parse_release(release_json(assets=[]))
    update_sources.GitHubSource(transport=FakeTransport()).download(release, tmp_path, done)
    assert results[0][0] is None and results[0][1]


def write_feed(folder, versions, payload=b"feed installer"):
    folder.mkdir(parents=True, exist_ok=True)
    entries = []
    for version in versions:
        name = f"SnipForge-{version}-setup.exe"
        (folder / name).write_bytes(payload + version.encode())
        entries.append(entry(version, "preview" if "-" in version else "stable", assets=[
            {"platform": "windows", "name": name, "sha256": updates.sha256_bytes(payload + version.encode())}]))
    (folder / "releases.json").write_text(json.dumps(manifest(*entries)), encoding="utf-8")


def test_folder_feed_on_a_real_folder(qapp, tmp_path):
    feed = tmp_path / "feed"
    write_feed(feed, ["0.6.0", "0.7.0-beta.1"])
    source = update_sources.FolderFeedSource(str(feed))
    results, done = collect()
    source.fetch(done)
    assert spin(lambda: results)
    found, error = results[0]
    assert error == "" and [r.version for r in found] == ["0.7.0-beta.1", "0.6.0"]
    downloads, done = collect()
    progress = []
    source.download(found[1], tmp_path / "downloads", done, lambda *a: progress.append(a))
    assert spin(lambda: downloads)
    path, error, digest = downloads[0]
    assert error == "" and path.read_bytes() == b"feed installer0.6.0" and progress
    assert digest == found[1].sha256


def test_folder_feed_rejects_tampered_and_outside_installers(qapp, tmp_path):
    feed = tmp_path / "feed"
    write_feed(feed, ["0.6.0"])
    source = update_sources.FolderFeedSource(str(feed / "releases.json"))
    results, done = collect()
    source.fetch(done)
    assert spin(lambda: results)
    release = results[0][0][0]
    (feed / release.installer_name).write_bytes(b"tampered")
    downloads, done = collect()
    source.download(release, tmp_path / "downloads", done)
    assert spin(lambda: downloads)
    assert downloads[0][0] is None and "checksum" in downloads[0][1]
    outside = replace(release, installer_url=str(tmp_path / "elsewhere" / release.installer_name))
    downloads, done = collect()
    source.download(outside, tmp_path / "downloads", done)
    assert spin(lambda: downloads)
    assert downloads[0][0] is None and "unexpected" in downloads[0][1]
    downloads, done = collect()
    source.download(replace(release, sha256=None), tmp_path / "downloads", done)
    assert spin(lambda: downloads)
    assert downloads[0][0] is None and "checksum" in downloads[0][1]


def test_url_feed_reads_manifest_and_installers_over_https(tmp_path):
    base = "https://it.example.com/snipforge/"
    data = b"web installer"
    body = manifest(entry("0.6.0", assets=[
        {"platform": "windows", "name": "SnipForge-0.6.0-setup.exe", "sha256": updates.sha256_bytes(data)}]))
    transport = FakeTransport({base + "releases.json": json.dumps(body).encode(),
                               base + "SnipForge-0.6.0-setup.exe": data})
    source = update_sources.FolderFeedSource(base, transport=transport)
    results, done = collect()
    source.fetch(done)
    release = results[0][0][0]
    assert release.installer_url == base + "SnipForge-0.6.0-setup.exe"
    downloads, done = collect()
    source.download(release, tmp_path, done)
    assert downloads[0][1] == "" and downloads[0][0].read_bytes() == data
    transport.responses[base + "SnipForge-0.6.0-setup.exe"] = (data, "http://it.example.com/downgraded.exe")
    downloads, done = collect()
    source.download(release, tmp_path, done)
    assert downloads[0][0] is None


def test_url_feed_explains_a_sign_in_or_a_missing_list():
    base = "https://contoso.sharepoint.com/sites/it/SnipForge/"
    cases = [(update_transport.NEEDS_SIGN_IN, ["sign-in", "synced folder", base]),
             (update_transport.NOT_FOUND, ["releases.json", base])]
    for error, words in cases:
        results, done = collect()
        transport = FakeTransport({base + "releases.json": FakeTransport.Error(error)})
        update_sources.FolderFeedSource(base, transport=transport).fetch(done)
        assert results[0][0] is None and all(word in results[0][1] for word in words), results[0][1]


def test_feed_sources_refuse_bad_locations():
    for bad in ("http://it.example.com/feed", r"relative\feed", ""):
        with pytest.raises(ValueError):
            update_sources.FolderFeedSource(bad)


def test_source_follows_policy():
    assert isinstance(update_sources.source_for(PolicyInfo()), update_sources.GitHubSource)
    feed = update_sources.source_for(PolicyInfo(source="feed", feed=r"\\srv\apps\SnipForge"))
    assert isinstance(feed, update_sources.FolderFeedSource) and "organization" in feed.label
    broken = update_sources.source_for(parse_policy([{"UpdateSource": "feed"}, {}]))
    results, done = collect()
    broken.fetch(done)
    assert results[0][0] is None and "UpdateFeed" in results[0][1]


# --- transports ---------------------------------------------------------------------------
class FakeReply:
    def __init__(self, body=b"", error=QNetworkReply.NoError, url="https://github.com/x", status=200, headers=None):
        self.body, self.err, self._url, self.status = body, error, url, status
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}

    def hasRawHeader(self, name):
        assert isinstance(name, str), "PySide6 takes header names as str"
        return name.lower() in self.headers

    def rawHeader(self, name):
        assert isinstance(name, str), "PySide6 takes header names as str"
        return self.headers.get(name.lower(), b"")

    def deleteLater(self):
        pass

    def error(self):
        return self.err

    def errorString(self):
        return "offline"

    def readAll(self):
        return self.body

    def url(self):
        return QUrl(self._url)

    def attribute(self, _):
        return self.status


def test_network_transport_handles_replies(qapp):
    transport = update_transport.NetworkTransport()
    cases = [
        (FakeReply(b"ok"), {}, (b"ok", "", "https://github.com/x")),
        (FakeReply(error=QNetworkReply.HostNotFoundError), {}, (None, "offline", "https://github.com/x")),
        (FakeReply(error=QNetworkReply.ContentAccessDenied, status=403, headers={"X-RateLimit-Remaining": b"0"}),
         {}, None),
        (FakeReply(b"x" * 20), {}, None),
        (FakeReply(error=QNetworkReply.OperationCanceledError), {"cancelled": True}, None),
        (FakeReply(error=QNetworkReply.OperationCanceledError), {}, None),
    ]
    outcomes = []
    for reply, flags, _ in cases:
        transfer = update_transport._ReplyTransfer(reply)
        transfer.cancelled = flags.get("cancelled", False)
        results, done = collect()
        transport._finished(reply, transfer, 10, done)
        outcomes.append(results[0])
    assert outcomes[0] == cases[0][2] and outcomes[1] == cases[1][2]
    assert outcomes[2][0] is None and "limiting" in outcomes[2][1]
    assert outcomes[3][0] is None and "larger" in outcomes[3][1]
    assert outcomes[4][1] == update_transport.CANCELED
    assert outcomes[5][0] is None and "timed out" in outcomes[5][1]


@pytest.mark.parametrize("status, error, headers, expected", [
    (401, QNetworkReply.AuthenticationRequiredError, {}, update_transport.NEEDS_SIGN_IN),
    (403, QNetworkReply.ContentAccessDenied, {}, update_transport.NEEDS_SIGN_IN),
    (403, QNetworkReply.ContentAccessDenied, {"Retry-After": b"60"}, update_transport.RATE_LIMITED),
    (403, QNetworkReply.ContentAccessDenied, {"X-RateLimit-Remaining": b"0"}, update_transport.RATE_LIMITED),
    (429, QNetworkReply.UnknownContentError, {}, update_transport.RATE_LIMITED),
    (404, QNetworkReply.ContentNotFoundError, {}, update_transport.NOT_FOUND),
    (500, QNetworkReply.InternalServerError, {}, "offline"),
])
def test_network_transport_names_http_refusals(qapp, status, error, headers, expected):
    reply = FakeReply(error=error, status=status, headers=headers)
    results, done = collect()
    update_transport.NetworkTransport()._finished(reply, update_transport._ReplyTransfer(reply), 10, done)
    assert results[0] == (None, expected, "https://github.com/x")


def test_network_transport_refuses_plain_http(qapp):
    results, done = collect()
    update_transport.NetworkTransport().get("http://github.com/x", timeout_ms=1000, max_bytes=10, done=done)
    assert spin(lambda: results)
    assert results[0][0] is None and "https" in results[0][1]


def test_file_transport_reads_with_progress_limits_and_errors(qapp, tmp_path):
    transport = update_transport.FileTransport()
    big = tmp_path / "big.bin"
    big.write_bytes(os.urandom(3 * 1024 * 1024 + 7))
    results, done = collect()
    progress = []
    transport.get(str(big), timeout_ms=5000, max_bytes=10_000_000, done=done, progress=lambda *a: progress.append(a))
    assert spin(lambda: results)
    assert results[0][0] == big.read_bytes() and results[0][1] == "" and progress[-1][0] == progress[-1][1]
    for path, limit, expected in ((big, 1000, "larger"), (tmp_path / "missing.json", 1000, "Couldn't read")):
        results, done = collect()
        transport.get(str(path), timeout_ms=5000, max_bytes=limit, done=done)
        assert spin(lambda: results)
        assert results[0][0] is None and expected in results[0][1]
    results, done = collect()
    transport.get(str(big), timeout_ms=5000, max_bytes=10_000_000, done=done).cancel()
    assert spin(lambda: results)
    assert results[0][1] == update_transport.CANCELED
    QCoreApplication.processEvents()
    assert len(results) == 1  # the worker's late result is dropped


def test_file_transport_times_out_when_a_share_stops_answering(qapp, tmp_path, monkeypatch):
    feed = tmp_path / "releases.json"
    feed.write_bytes(b"{}")
    seen = []

    def stuck(path, max_bytes, transfer, report):
        report(1, 2)  # some progress, then nothing
        while not transfer.cancelled:
            time.sleep(0.01)
        seen.append("worker stopped")
        return None

    monkeypatch.setattr(update_transport, "_read", stuck)
    results, done = collect()
    update_transport.FileTransport().get(str(feed), timeout_ms=150, max_bytes=10, done=done)
    assert spin(lambda: results and seen)
    assert results[0][0] is None and "timed out" in results[0][1] and len(results) == 1


# --- platform -----------------------------------------------------------------------------
def test_install_mode():
    from pathlib import Path
    assert update_platform.install_mode(frozen=False) == "source"
    per_user = Path.home() / "AppData" / "Local" / "Programs" / "SnipForge" / "SnipForge.exe"
    assert update_platform.install_mode(str(per_user), frozen=True) == "per-user"
    assert update_platform.install_mode(r"C:\Program Files\SnipForge\SnipForge.exe", frozen=True) == "all-users"


def test_installer_names_by_platform():
    assert update_platform.installer_version("SnipForge-0.6.0-beta.1-setup.exe") == "0.6.0-beta.1"
    assert update_platform.installer_version("SnipForge-0.6.0-portable.zip") is None
    assert update_platform.installer_version("SnipForge-0.6.0.dmg", platform="macos") == "0.6.0"
    assert update_platform.installer_suffix_ok("SnipForge-0.6.0-setup.exe")
    assert not update_platform.installer_suffix_ok("SnipForge-0.6.0.dmg")


def test_launch_installer_runs_silently(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(update_platform.subprocess, "Popen", lambda args, **k: calls.append(args))
    exe = tmp_path / "SnipForge-9.0.0-setup.exe"
    exe.write_bytes(b"x")
    assert update_platform.launch_installer(exe) == ""
    assert calls[0][0] == str(exe) and "/VERYSILENT" in calls[0] and "/CURRENTUSER" in calls[0]
    assert "/RELAUNCH=0" not in calls[0]
    assert update_platform.launch_installer(exe, relaunch=False) == ""
    assert "/RELAUNCH=0" in calls[1]

    def blocked(*a, **k):
        raise OSError("blocked by policy")

    monkeypatch.setattr(update_platform.subprocess, "Popen", blocked)
    assert "blocked" in update_platform.launch_installer(exe)
