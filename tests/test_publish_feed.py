"""scripts/publish_feed.py: mirrors releases into a folder feed that the in-app updater reads."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from snipforge import project

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pf = _load("publish_feed")

API_ROOT = f"{project.API_URL}/repos/"
API = f"{API_ROOT}{project.REPO}/releases/"
DL = f"{project.RELEASES_URL}/download/"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_dist(folder: Path, version: str, payload: bytes = b"installer", sums_override: str | None = None) -> Path:
    """A dist folder like scripts/build.py leaves: versioned + version-less installer, zip, sums."""
    folder.mkdir(parents=True, exist_ok=True)
    installer = f"SnipForge-{version}-setup.exe"
    (folder / installer).write_bytes(payload)
    (folder / "SnipForge-setup.exe").write_bytes(payload)
    (folder / f"SnipForge-{version}-portable.zip").write_bytes(b"zip")
    digest = sums_override or sha(payload)
    lines = [f"{sha(b'zip')}  SnipForge-{version}-portable.zip", f"{digest}  {installer}",
             f"{sha(payload)}  SnipForge-setup.exe"]
    (folder / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return folder


def publish_dist(dist, target, *extra):
    return pf.main(["--dist", str(dist), "--target", str(target), *extra])


def read_feed(target: Path) -> dict:
    return json.loads((target / "releases.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ versions


@pytest.mark.parametrize("newer, older", [
    ("0.6.0", "0.5.0"),
    ("0.6.0", "0.6.0-preview.2"),
    ("0.6.0-preview.2", "0.6.0-preview.1"),
    ("0.6.0-preview.10", "0.6.0-preview.9"),
    ("0.6.0-preview.1", "0.5.9"),
    ("1.0.0", "0.10.0"),
])
def test_version_order(newer, older):
    assert pf.version_key(newer) > pf.version_key(older)


@pytest.mark.parametrize("bad", ["", "v0.6.0", "0.6", "0.6.0-beta.1", "0.6.0-preview", "0.6.0 ", "0.6.0-preview.1x"])
def test_rejects_unknown_version_formats(bad):
    with pytest.raises(pf.FeedError):
        pf.version_key(bad)


def test_channel_follows_version():
    assert pf.channel_for("0.6.0") == "stable"
    assert pf.channel_for("0.6.0-preview.3") == "preview"


def test_parse_sums_reads_sha256sum_format():
    a, b = "a" * 64, "B" * 64
    sums = pf.parse_sums(f"{a}  one.exe\n{b} *two.zip\n\nnot a line\n{'c' * 63}  short.exe\n")
    assert sums == {"one.exe": a, "two.zip": b.lower()}


# ------------------------------------------------------------------ publishing from a dist folder


def test_publishes_installer_and_writes_feed(tmp_path):
    dist = make_dist(tmp_path / "dist", "0.6.0", b"v060")
    target = tmp_path / "feed"
    target.mkdir()

    assert publish_dist(dist, target, "--notes", "Faster crop", "--published", "2026-10-20T15:00:00Z") == 0

    assert (target / "SnipForge-0.6.0-setup.exe").read_bytes() == b"v060"
    assert not (target / "SnipForge-setup.exe").exists()  # only versioned installers go in the feed
    assert not (target / "SnipForge-0.6.0-portable.zip").exists()
    assert read_feed(target) == {
        "schema": 1,
        "releases": [{
            "version": "0.6.0",
            "channel": "stable",
            "published": "2026-10-20T15:00:00Z",
            "notes": "Faster crop",
            "assets": [{"platform": "windows", "name": "SnipForge-0.6.0-setup.exe", "sha256": sha(b"v060")}],
        }],
    }
    assert sorted(p.name for p in target.iterdir()) == ["SnipForge-0.6.0-setup.exe", "releases.json"]


def test_creates_missing_target_folder(tmp_path):
    dist = make_dist(tmp_path / "dist", "0.6.0")
    target = tmp_path / "new" / "feed"
    assert publish_dist(dist, target) == 0
    assert read_feed(target)["releases"][0]["version"] == "0.6.0"


def test_rerun_is_idempotent(tmp_path):
    dist = make_dist(tmp_path / "dist", "0.6.0")
    target = tmp_path / "feed"
    assert publish_dist(dist, target, "--notes-file", str(_notes(tmp_path, "Notes"))) == 0
    first = (target / "releases.json").read_bytes()
    mtime = (target / "releases.json").stat().st_mtime_ns

    assert publish_dist(dist, target) == 0  # no notes/published given: keep what's there

    assert (target / "releases.json").read_bytes() == first
    assert (target / "releases.json").stat().st_mtime_ns == mtime  # not rewritten, so sync clients stay quiet
    assert len(read_feed(target)["releases"]) == 1
    assert read_feed(target)["releases"][0]["notes"] == "Notes"


def test_new_releases_merge_newest_first(tmp_path):
    target = tmp_path / "feed"
    for version in ["0.5.0", "0.6.0-preview.1", "0.6.0", "0.6.0-preview.2"]:
        dist = make_dist(tmp_path / version, version, version.encode())
        assert publish_dist(dist, target) == 0

    releases = read_feed(target)["releases"]
    assert [r["version"] for r in releases] == ["0.6.0", "0.6.0-preview.2", "0.6.0-preview.1", "0.5.0"]
    assert [r["channel"] for r in releases] == ["stable", "preview", "preview", "stable"]
    for r in releases:  # every installer the feed lists is in the folder with the listed hash
        asset = r["assets"][0]
        assert sha((target / asset["name"]).read_bytes()) == asset["sha256"]


def test_keeps_unrelated_existing_entries(tmp_path):
    target = tmp_path / "feed"
    target.mkdir()
    old = {"version": "0.4.0", "channel": "stable", "published": "2026-10-14T12:19:36Z", "notes": "first",
           "assets": [{"platform": "windows", "name": "SnipForge-0.4.0-setup.exe", "sha256": "0" * 64}]}
    (target / "releases.json").write_text(json.dumps({"schema": 1, "releases": [old]}), encoding="utf-8")

    assert publish_dist(make_dist(tmp_path / "dist", "0.5.0"), target) == 0

    assert read_feed(target)["releases"][1] == old


def test_published_defaults_to_now_in_utc(tmp_path):
    target = tmp_path / "feed"
    assert publish_dist(make_dist(tmp_path / "dist", "0.6.0"), target) == 0
    published = read_feed(target)["releases"][0]["published"]
    assert published.endswith("Z") and pf.parse_timestamp(published)


def test_explicit_values_update_an_existing_entry(tmp_path):
    dist = make_dist(tmp_path / "dist", "0.6.0")
    target = tmp_path / "feed"
    assert publish_dist(dist, target, "--notes", "old", "--published", "2026-10-20T15:00:00Z") == 0
    assert publish_dist(dist, target, "--notes", "new", "--published", "2026-10-21T09:30:00+02:00") == 0
    entry = read_feed(target)["releases"][0]
    assert (entry["notes"], entry["published"]) == ("new", "2026-10-21T07:30:00Z")


def test_channel_override_and_promotion(tmp_path):
    dist = make_dist(tmp_path / "dist", "0.6.0")
    target = tmp_path / "feed"
    assert publish_dist(dist, target, "--channel", "preview") == 0
    assert read_feed(target)["releases"][0]["channel"] == "preview"
    assert publish_dist(dist, target, "--channel", "stable") == 0  # promote the same build
    assert read_feed(target)["releases"][0]["channel"] == "stable"


def test_preview_build_cannot_be_published_as_stable(tmp_path):
    dist = make_dist(tmp_path / "dist", "0.6.0-preview.1")
    assert publish_dist(dist, tmp_path / "feed", "--channel", "stable") == 1
    assert not (tmp_path / "feed" / "releases.json").exists()


# ------------------------------------------------------------------ refusing bad input


def test_checksum_mismatch_writes_nothing(tmp_path, capsys):
    dist = make_dist(tmp_path / "dist", "0.6.0", b"tampered", sums_override=sha(b"original"))
    target = tmp_path / "feed"
    assert publish_dist(dist, target) == 1
    assert "SHA-256" in capsys.readouterr().err
    assert not target.exists() or list(target.iterdir()) == []


def test_installer_missing_from_sums_is_an_error(tmp_path):
    dist = make_dist(tmp_path / "dist", "0.6.0")
    (dist / "SHA256SUMS.txt").write_text(f"{sha(b'zip')}  SnipForge-0.6.0-portable.zip\n", encoding="utf-8")
    assert publish_dist(dist, tmp_path / "feed") == 1
    assert not (tmp_path / "feed" / "releases.json").exists()


def test_missing_sums_file_is_an_error(tmp_path):
    dist = make_dist(tmp_path / "dist", "0.6.0")
    (dist / "SHA256SUMS.txt").unlink()
    assert publish_dist(dist, tmp_path / "feed") == 1


def test_empty_dist_is_an_error(tmp_path, capsys):
    (tmp_path / "dist").mkdir()
    assert publish_dist(tmp_path / "dist", tmp_path / "feed") == 1
    assert "No SnipForge-<version>-setup.exe" in capsys.readouterr().err


def test_several_versions_in_dist_need_version_flag(tmp_path):
    dist = make_dist(tmp_path / "dist", "0.6.0", b"new")
    make_dist(dist, "0.5.0", b"old")
    assert publish_dist(dist, tmp_path / "feed") == 1
    assert publish_dist(dist, tmp_path / "feed", "--version", "0.5.0") == 0
    assert [r["version"] for r in read_feed(tmp_path / "feed")["releases"]] == ["0.5.0"]


def test_changed_installer_for_published_version_needs_replace(tmp_path, capsys):
    target = tmp_path / "feed"
    assert publish_dist(make_dist(tmp_path / "a", "0.6.0", b"first build"), target) == 0
    before = (target / "releases.json").read_bytes()
    rebuilt = make_dist(tmp_path / "b", "0.6.0", b"second build")

    assert publish_dist(rebuilt, target) == 1
    assert "--replace" in capsys.readouterr().err
    assert (target / "SnipForge-0.6.0-setup.exe").read_bytes() == b"first build"
    assert (target / "releases.json").read_bytes() == before

    assert publish_dist(rebuilt, target, "--replace") == 0
    assert (target / "SnipForge-0.6.0-setup.exe").read_bytes() == b"second build"
    assert read_feed(target)["releases"][0]["assets"][0]["sha256"] == sha(b"second build")


@pytest.mark.parametrize("content", [
    "not json",
    json.dumps({"schema": 2, "releases": []}),
    json.dumps({"schema": 1}),
    json.dumps({"schema": 1, "releases": [{"version": "0.5.0"}]}),
    json.dumps({"schema": 1, "releases": [{"version": "0.5.0", "channel": "beta", "published": "2026-10-14T00:00:00Z",
                                           "notes": "", "assets": []}]}),
])
def test_never_overwrites_a_feed_it_does_not_understand(tmp_path, content):
    target = tmp_path / "feed"
    target.mkdir()
    (target / "releases.json").write_text(content, encoding="utf-8")
    assert publish_dist(make_dist(tmp_path / "dist", "0.6.0"), target) == 1
    assert (target / "releases.json").read_text(encoding="utf-8") == content
    assert not (target / "SnipForge-0.6.0-setup.exe").exists()


def test_bad_published_timestamp_is_rejected(tmp_path):
    assert publish_dist(make_dist(tmp_path / "dist", "0.6.0"), tmp_path / "feed", "--published", "yesterday") == 1


def test_json_write_is_atomic(tmp_path, monkeypatch):
    target = tmp_path / "feed"
    assert publish_dist(make_dist(tmp_path / "a", "0.5.0"), target) == 0
    before = (target / "releases.json").read_bytes()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(pf.os, "replace", boom)
    with pytest.raises(OSError):
        pf.write_json_atomic(target / "releases.json", {"schema": 1, "releases": []})
    assert (target / "releases.json").read_bytes() == before
    assert sorted(p.name for p in target.iterdir()) == ["SnipForge-0.5.0-setup.exe", "releases.json"]


# ------------------------------------------------------------------ publishing from GitHub


def gh_release(tag="v0.6.0-preview.1", prerelease=True, assets=None, body="### Features\n* faster crop"):
    version = tag[1:]
    installer = f"SnipForge-{version}-setup.exe"
    return {
        "tag_name": tag,
        "prerelease": prerelease,
        "draft": False,
        "published_at": "2026-10-20T15:00:00Z",
        "body": body,
        "assets": assets if assets is not None else [
            {"name": installer, "browser_download_url": f"{DL}{tag}/{installer}"},
            {"name": "SnipForge-setup.exe", "browser_download_url": f"{DL}{tag}/SnipForge-setup.exe"},
            {"name": "SHA256SUMS.txt", "browser_download_url": f"{DL}{tag}/SHA256SUMS.txt"},
        ],
    }


class FakeGitHub:
    def __init__(self, release, files):
        self.release, self.files, self.urls, self.api_roots, self.hosts = release, files, [], [], []

    def fetch_json(self, url, api_root):
        self.urls.append(url)
        self.api_roots.append(api_root)
        return self.release

    def download(self, url, dest, hosts):
        self.urls.append(url)
        self.hosts.append(hosts)
        dest.write_bytes(self.files[url.rsplit("/", 1)[1]])


def fake_github(monkeypatch, release, payload=b"preview build", sums=None):
    version = release["tag_name"][1:]
    installer = f"SnipForge-{version}-setup.exe"
    files = {installer: payload, "SnipForge-setup.exe": payload,
             "SHA256SUMS.txt": (sums or f"{sha(payload)}  {installer}\n").encode()}
    fake = FakeGitHub(release, files)
    monkeypatch.setattr(pf, "fetch_json", fake.fetch_json)
    monkeypatch.setattr(pf, "download", fake.download)
    return fake


def test_publishes_a_github_prerelease_to_the_preview_channel(tmp_path, monkeypatch):
    fake = fake_github(monkeypatch, gh_release())
    target = tmp_path / "feed"

    assert pf.main(["--github", "0.6.0-preview.1", "--target", str(target)]) == 0

    assert fake.urls[0] == API + "tags/v0.6.0-preview.1"
    assert "SnipForge-setup.exe" not in " ".join(fake.urls)  # only what the feed needs is downloaded
    assert read_feed(target)["releases"] == [{
        "version": "0.6.0-preview.1",
        "channel": "preview",
        "published": "2026-10-20T15:00:00Z",
        "notes": "### Features\n* faster crop",
        "assets": [{"platform": "windows", "name": "SnipForge-0.6.0-preview.1-setup.exe",
                    "sha256": sha(b"preview build")}],
    }]
    assert (target / "SnipForge-0.6.0-preview.1-setup.exe").read_bytes() == b"preview build"


def test_latest_means_the_latest_stable_release(tmp_path, monkeypatch):
    fake = fake_github(monkeypatch, gh_release("v0.6.0", prerelease=False))
    assert pf.main(["--github", "latest", "--target", str(tmp_path / "feed")]) == 0
    assert fake.urls[0] == API + "latest"
    assert read_feed(tmp_path / "feed")["releases"][0]["channel"] == "stable"


def test_github_rerun_is_idempotent(tmp_path, monkeypatch):
    fake_github(monkeypatch, gh_release("v0.6.0", prerelease=False))
    target = tmp_path / "feed"
    assert pf.main(["--github", "v0.6.0", "--target", str(target)]) == 0
    first = (target / "releases.json").read_bytes()
    assert pf.main(["--github", "v0.6.0", "--target", str(target)]) == 0
    assert (target / "releases.json").read_bytes() == first


def test_github_checksum_mismatch_writes_nothing(tmp_path, monkeypatch):
    fake_github(monkeypatch, gh_release(), sums=f"{sha(b'other')}  SnipForge-0.6.0-preview.1-setup.exe\n")
    target = tmp_path / "feed"
    assert pf.main(["--github", "v0.6.0-preview.1", "--target", str(target)]) == 1
    assert not target.exists() or list(target.iterdir()) == []


def test_github_asset_from_an_unexpected_host_is_refused(tmp_path, monkeypatch, capsys):
    release = gh_release(assets=[
        {"name": "SnipForge-0.6.0-preview.1-setup.exe", "browser_download_url": "https://evil.example/x.exe"},
        {"name": "SHA256SUMS.txt", "browser_download_url": f"{DL}v0.6.0-preview.1/SHA256SUMS.txt"},
    ])
    fake = fake_github(monkeypatch, release)
    assert pf.main(["--github", "v0.6.0-preview.1", "--target", str(tmp_path / "feed")]) == 1
    assert "evil.example" not in " ".join(fake.urls)
    assert "SnipForge-0.6.0-preview.1-setup.exe" in capsys.readouterr().err


def test_github_release_without_installer_is_an_error(tmp_path, monkeypatch):
    fake_github(monkeypatch, gh_release(assets=[]))
    assert pf.main(["--github", "v0.6.0-preview.1", "--target", str(tmp_path / "feed")]) == 1


def test_github_draft_is_refused(tmp_path, monkeypatch):
    release = gh_release()
    release["draft"] = True
    fake_github(monkeypatch, release)
    assert pf.main(["--github", "v0.6.0-preview.1", "--target", str(tmp_path / "feed")]) == 1


@pytest.mark.parametrize("tag", ["v0.6", "../../etc", "v0.6.0?x=1", "main"])
def test_github_tag_is_validated_before_any_request(tmp_path, monkeypatch, tag):
    fake = fake_github(monkeypatch, gh_release())
    assert pf.main(["--github", tag, "--target", str(tmp_path / "feed")]) == 1
    assert fake.urls == []


def test_repo_option_is_validated(tmp_path, monkeypatch):
    fake = fake_github(monkeypatch, gh_release())
    assert pf.main(["--github", "latest", "--repo", "x/y/../z", "--target", str(tmp_path / "feed")]) == 1
    assert fake.urls == []


def test_github_enterprise_server_and_repository(tmp_path, monkeypatch):
    ghe = "https://ghe.example.test/fixture-org/snipforge/releases/download/"
    release = gh_release("v0.6.0", prerelease=False)
    for item in release["assets"]:
        item["browser_download_url"] = item["browser_download_url"].replace(DL, ghe)
    fake = fake_github(monkeypatch, release)
    assert pf.main(["--github", "latest", "--server-url", "https://ghe.example.test", "--repo", "fixture-org/snipforge",
                    "--target", str(tmp_path / "feed")]) == 0
    assert fake.urls[0] == "https://ghe.example.test/api/v3/repos/fixture-org/snipforge/releases/latest"
    assert fake.api_roots == ["https://ghe.example.test/api/v3/repos/"]
    assert fake.hosts[0] == project.download_hosts("https://ghe.example.test")
    assert all(url.startswith(ghe) for url in fake.urls[1:])


def test_defaults_come_from_the_app(tmp_path, monkeypatch):
    fake = fake_github(monkeypatch, gh_release("v0.6.0", prerelease=False))
    assert pf.main(["--github", "latest", "--target", str(tmp_path / "feed")]) == 0
    assert fake.api_roots == [API_ROOT] and fake.hosts[0] == project.DOWNLOAD_HOSTS


def test_server_url_option_is_validated(tmp_path, monkeypatch, capsys):
    fake = fake_github(monkeypatch, gh_release())
    assert pf.main(["--github", "latest", "--server-url", "http://ghe.example.test",
                    "--target", str(tmp_path / "feed")]) == 1
    assert fake.urls == [] and "https://" in capsys.readouterr().err


def test_dist_and_github_are_mutually_exclusive(tmp_path):
    with pytest.raises(SystemExit):
        pf.main(["--target", str(tmp_path)])
    with pytest.raises(SystemExit):
        pf.main(["--dist", "d", "--github", "latest", "--target", str(tmp_path)])


def _notes(folder: Path, text: str) -> Path:
    path = folder / "notes.md"
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------------ the real network helpers (urlopen faked)


class FakeResponse:
    def __init__(self, body=b"", final_url=DL + "v0.6.0/x"):
        self.body, self.final_url, self.pos = body, final_url, 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def geturl(self):
        return self.final_url

    def read(self, size=-1):
        chunk = self.body[self.pos:] if size < 0 else self.body[self.pos:self.pos + size]
        self.pos += len(chunk)
        return chunk


def test_fetch_json_reads_the_api(monkeypatch):
    seen = []

    def urlopen(request, timeout):
        seen.append(request)
        return FakeResponse(b'{"tag_name": "v0.6.0"}')

    monkeypatch.setattr(pf.urllib.request, "urlopen", urlopen)
    assert pf.fetch_json(API + "latest", API_ROOT) == {"tag_name": "v0.6.0"}
    assert seen[0].get_header("User-agent") == "snipforge-publish-feed"


def test_fetch_json_reports_missing_release(monkeypatch):
    def urlopen(request, timeout):
        raise pf.urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(pf.urllib.request, "urlopen", urlopen)
    with pytest.raises(pf.FeedError, match="no published release"):
        pf.fetch_json(API + "tags/v9.9.9", API_ROOT)


@pytest.mark.parametrize("status", [401, 403, 404])
def test_fetch_json_suggests_dist_for_a_private_repository(monkeypatch, status):
    """This script doesn't sign in, so a private repository's releases are downloaded with gh first."""
    def urlopen(request, timeout):
        raise pf.urllib.error.HTTPError(request.full_url, status, "Nope", {}, None)

    monkeypatch.setattr(pf.urllib.request, "urlopen", urlopen)
    with pytest.raises(pf.FeedError, match="gh release download.*--dist"):
        pf.fetch_json(API + "latest", API_ROOT)


def test_fetch_json_only_talks_to_the_github_api():
    with pytest.raises(pf.FeedError):
        pf.fetch_json("file:///C:/Windows/win.ini", API_ROOT)
    with pytest.raises(pf.FeedError):
        pf.fetch_json("https://api.github.com.evil.example/repos/x", "https://api.github.com/repos/")


def test_download_streams_to_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(pf.urllib.request, "urlopen", lambda request, timeout: FakeResponse(b"bytes" * 1000))
    pf.download(DL + "v0.6.0/SHA256SUMS.txt", tmp_path / "out", project.DOWNLOAD_HOSTS)
    assert (tmp_path / "out").read_bytes() == b"bytes" * 1000


def test_download_refuses_untrusted_links_and_redirects(tmp_path, monkeypatch):
    with pytest.raises(pf.FeedError):
        pf.download(DL.replace("https://", "http://") + "x.exe", tmp_path / "a", project.DOWNLOAD_HOSTS)
    monkeypatch.setattr(pf.urllib.request, "urlopen",
                        lambda request, timeout: FakeResponse(b"x", final_url="https://evil.example/x"))
    with pytest.raises(pf.FeedError, match="unexpected host"):
        pf.download(DL + "v0.6.0/x.exe", tmp_path / "b", project.DOWNLOAD_HOSTS)
    assert not (tmp_path / "b").exists()


def test_download_network_failure_is_a_feed_error(tmp_path, monkeypatch):
    def urlopen(request, timeout):
        raise pf.urllib.error.URLError("proxy said no")

    monkeypatch.setattr(pf.urllib.request, "urlopen", urlopen)
    with pytest.raises(pf.FeedError, match="proxy said no"):
        pf.download(DL + "v0.6.0/x.exe", tmp_path / "c", project.DOWNLOAD_HOSTS)


# ------------------------------------------------------------------ real-world folders (OneDrive, shares, Notepad)


def test_unwritable_target_is_a_clean_error(tmp_path, capsys):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("file", encoding="utf-8")
    assert publish_dist(make_dist(tmp_path / "dist", "0.6.0"), blocker / "feed") == 1
    assert capsys.readouterr().err.startswith("error:")


def test_rename_retries_while_sync_holds_the_file(tmp_path, monkeypatch):
    real_replace, calls = pf.os.replace, []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) < 3:
            raise PermissionError(32, "The process cannot access the file because it is being used")
        real_replace(src, dst)

    monkeypatch.setattr(pf.os, "replace", flaky)
    monkeypatch.setattr(pf, "RETRY_DELAY_S", 0)
    assert pf.write_json_atomic(tmp_path / "releases.json", {"schema": 1, "releases": []})
    assert len(calls) == 3 and json.loads((tmp_path / "releases.json").read_text(encoding="utf-8"))["schema"] == 1


def test_notepad_byte_order_marks_are_ignored(tmp_path):
    target = tmp_path / "feed"
    target.mkdir()
    (target / "releases.json").write_text(json.dumps({"schema": 1, "releases": []}), encoding="utf-8-sig")
    notes = tmp_path / "notes.txt"
    notes.write_text("Faster crop", encoding="utf-8-sig")
    assert publish_dist(make_dist(tmp_path / "dist", "0.6.0"), target, "--notes-file", str(notes)) == 0
    assert read_feed(target)["releases"][0]["notes"] == "Faster crop"


def test_hash_case_in_an_existing_feed_does_not_matter(tmp_path):
    target = tmp_path / "feed"
    assert publish_dist(make_dist(tmp_path / "dist", "0.6.0", b"same"), target) == 0
    feed = read_feed(target)
    feed["releases"][0]["assets"][0]["sha256"] = sha(b"same").upper()
    (target / "releases.json").write_text(json.dumps(feed), encoding="utf-8")
    assert publish_dist(make_dist(tmp_path / "dist", "0.6.0", b"same"), target) == 0
    assert read_feed(target)["releases"][0]["assets"][0]["sha256"] == sha(b"same")
