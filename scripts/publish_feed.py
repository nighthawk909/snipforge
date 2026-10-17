"""Mirror SnipForge releases into a folder feed (a SharePoint library, a network share or any folder)
that the in-app updater can read instead of GitHub.

    python scripts/publish_feed.py --github latest --target "\\\\server\\share\\SnipForge"
    python scripts/publish_feed.py --github v0.6.0-preview.1 --target "C:\\Users\\me\\Company\\SnipForge"
    python scripts/publish_feed.py --dist dist --target D:\\feed --notes-file notes.md

--github reads the repository snipforge/project.py names (or --server-url and --repo) without
signing in. For a private repository, download the release with gh first and use --dist:

    gh release download v0.6.0 --repo <host>/<owner>/<repo> --dir dist
    gh release view v0.6.0 --repo <host>/<owner>/<repo> --json body --jq .body > notes.md

Each installer is checked against the release's SHA256SUMS.txt, copied into the target folder and
checked again there; only then is the release merged into <target>/releases.json (newest first,
written atomically). Re-running with the same release changes nothing. Needs only the Python
standard library (and snipforge/project.py next door).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from snipforge import project  # noqa: E402 - standard library only; names the repository

SCHEMA = 1
FEED_FILE = "releases.json"
SUMS_FILE = "SHA256SUMS.txt"
CHANNELS = ("stable", "preview")
# One installer per platform; the updater picks the asset for the platform it runs on.
INSTALLER_NAMES = {"windows": "SnipForge-{version}-setup.exe"}
INSTALLER_RE = re.compile(r"SnipForge-(.+)-setup\.exe")  # the version-less SnipForge-setup.exe doesn't match
VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-preview\.(\d+))?")
TAG_RE = re.compile(r"v?(\d+\.\d+\.\d+(?:-preview\.\d+)?)")
SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")
SAFE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
USER_AGENT = "snipforge-publish-feed"
TIMEOUT_S = 60
CHUNK = 1 << 20
RETRIES = 10
RETRY_DELAY_S = 0.5


class FeedError(Exception):
    """Something is wrong with the input or the target; nothing was published."""


@dataclass(frozen=True)
class Asset:
    platform: str
    name: str
    source: Path   # local file to copy into the feed
    sha256: str    # expected digest, from SHA256SUMS.txt


@dataclass(frozen=True)
class Release:
    version: str
    channel: str
    published: str | None   # None: keep the feed's value, or now for a new entry
    notes: str | None       # None: keep the feed's value, or "" for a new entry
    assets: tuple[Asset, ...]


# ------------------------------------------------------------------ versions, checksums, timestamps


def version_key(version: str) -> tuple[int, ...]:
    """Sort key: 0.6.0 > 0.6.0-preview.2 > 0.6.0-preview.1 > 0.5.0."""
    match = VERSION_RE.fullmatch(version or "")
    if not match:
        raise FeedError(f"{version!r} isn't a SnipForge version (expected 1.2.3 or 1.2.3-preview.4)")
    major, minor, patch, preview = match.groups()
    stable = preview is None
    return int(major), int(minor), int(patch), int(stable), 0 if stable else int(preview)


def channel_for(version: str) -> str:
    return "stable" if version_key(version)[3] else "preview"


def resolve_channel(version: str, requested: str | None, default: str) -> str:
    channel = requested or default
    if channel == "stable" and channel_for(version) == "preview":
        raise FeedError(f"{version} is a preview build and can't be published to the stable channel")
    return channel


def parse_sums(text: str) -> dict[str, str]:
    """Read `sha256sum` output ("<hex>  name" or "<hex> *name") into {name: lowercase hex}."""
    sums = {}
    for line in text.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and SHA256_RE.fullmatch(parts[0]):
            sums[parts[1].lstrip("*")] = parts[0].lower()
    return sums


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_timestamp(text: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError) as e:
        raise FeedError(f"{text!r} isn't an ISO-8601 time like 2026-10-20T15:00:00Z") from e
    if moment.tzinfo is None:
        raise FeedError(f"{text!r} has no time zone; add Z for UTC or an offset like +02:00")
    return moment


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ the feed file


def validate_entry(entry: object) -> None:
    if not isinstance(entry, dict):
        raise FeedError("a release entry isn't an object")
    version = entry.get("version")
    version_key(version if isinstance(version, str) else "")
    if entry.get("channel") not in CHANNELS:
        raise FeedError(f"{version}: channel must be one of {', '.join(CHANNELS)}")
    if not isinstance(entry.get("published"), str):
        raise FeedError(f"{version}: published must be an ISO-8601 time")
    parse_timestamp(entry["published"])
    if not isinstance(entry.get("notes"), str):
        raise FeedError(f"{version}: notes must be text")
    assets = entry.get("assets")
    if not isinstance(assets, list) or not assets:
        raise FeedError(f"{version}: assets must be a non-empty list")
    for asset in assets:
        if not (isinstance(asset, dict) and isinstance(asset.get("platform"), str)
                and isinstance(asset.get("name"), str) and SAFE_NAME_RE.fullmatch(asset["name"])
                and isinstance(asset.get("sha256"), str) and SHA256_RE.fullmatch(asset["sha256"])):
            raise FeedError(f"{version}: each asset needs platform, a plain file name and a 64-digit sha256")


def validate_feed(feed: object) -> dict:
    if not isinstance(feed, dict) or feed.get("schema") != SCHEMA or not isinstance(feed.get("releases"), list):
        raise FeedError(f'expected {{"schema": {SCHEMA}, "releases": [...]}}')
    seen = set()
    for entry in feed["releases"]:
        validate_entry(entry)
        if entry["version"] in seen:
            raise FeedError(f"{entry['version']} is listed twice")
        seen.add(entry["version"])
    return feed


def load_feed(path: Path) -> dict:
    """The feed at `path`, or an empty one. Refuses files it doesn't understand rather than replace them."""
    if not path.exists():
        return {"schema": SCHEMA, "releases": []}
    try:
        feed = json.loads(path.read_text(encoding="utf-8-sig"))  # tolerate a BOM from Notepad
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise FeedError(f"Couldn't read {path}: {e}. Fix or move it; it was left unchanged.") from e
    try:
        return validate_feed(feed)
    except FeedError as e:
        raise FeedError(f"{path} isn't a SnipForge feed this script understands ({e}); "
                        "it was left unchanged") from e


def merge_entry(feed: dict, entry: dict) -> dict:
    """A new feed with `entry` added or replacing the same version, newest first."""
    others = [r for r in feed["releases"] if r["version"] != entry["version"]]
    releases = sorted([*others, entry], key=lambda r: version_key(r["version"]), reverse=True)
    return {"schema": SCHEMA, "releases": releases}


def write_json_atomic(path: Path, data: dict) -> bool:
    """Write `data` as JSON through a temp file and a rename, so readers never see half a file.
    Returns False (and leaves the file alone) when the content is already identical."""
    encoded = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    if path.exists() and path.read_bytes() == encoded:
        return False
    # OneDrive and SharePoint sync skip .tmp files, so a half-written temp file is never uploaded.
    fd, tmp = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)  # mkstemp makes it owner-only; feed readers need to read it
        replace_file(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return True


def replace_file(src: str | Path, dst: Path) -> None:
    """os.replace, retried briefly: OneDrive and virus scanners hold files open for a moment."""
    for attempt in range(RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == RETRIES - 1:
                raise
            time.sleep(RETRY_DELAY_S)


# ------------------------------------------------------------------ release sources


def release_from_dist(dist: Path, version: str | None, channel: str | None, notes: str | None,
                      published: str | None) -> Release:
    """The release in a build output folder (what scripts/build.py --installer leaves in dist/)."""
    if not dist.is_dir():
        raise FeedError(f"{dist} isn't a folder")
    found = sorted({m.group(1) for p in dist.iterdir() if (m := INSTALLER_RE.fullmatch(p.name))
                    and VERSION_RE.fullmatch(m.group(1))}, key=version_key)
    if version is None:
        if not found:
            raise FeedError(f"No SnipForge-<version>-setup.exe in {dist}")
        if len(found) > 1:
            raise FeedError(f"{dist} has several versions ({', '.join(found)}); pick one with --version")
        version = found[0]
    version_key(version)
    sums_path = dist / SUMS_FILE
    if not sums_path.is_file():
        raise FeedError(f"{sums_path} is missing; build with scripts/build.py, which writes it")
    sums = parse_sums(sums_path.read_text(encoding="utf-8-sig"))
    assets = []
    for platform, template in INSTALLER_NAMES.items():
        name = template.format(version=version)
        if not (dist / name).is_file():
            continue
        if name not in sums:
            raise FeedError(f"{name} isn't listed in {sums_path}")
        assets.append(Asset(platform, name, dist / name, sums[name]))
    if not assets:
        raise FeedError(f"No installer for {version} in {dist}")
    return Release(version, resolve_channel(version, channel, channel_for(version)), published, notes, tuple(assets))


def trusted_download(url: str, hosts) -> bool:
    parsed = urlparse(url or "")
    return parsed.scheme == "https" and parsed.hostname in hosts


def fetch_json(url: str, api_root: str) -> dict:
    """GET a GitHub API URL under `api_root` (https://<api>/repos/), without signing in."""
    if not (api_root.startswith("https://") and api_root.endswith("/repos/") and url.startswith(api_root)):
        raise FeedError(f"Refusing to query {url}")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT, "X-GitHub-Api-Version": "2022-11-28"}
    # S310: only the GitHub API (checked above) and the server's download hosts are ever opened.
    request = urllib.request.Request(url, headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
            return json.load(response)
    except urllib.error.HTTPError as e:
        private = ("If the repository is private, download the release with `gh release download <tag> "
                   "--dir <folder>` and publish it with --dist <folder>.")
        if e.code == 404:
            raise FeedError(f"GitHub has no published release at {url}. {private}") from e
        if e.code in (401, 403):
            raise FeedError(f"GitHub returned HTTP {e.code} for {url}; this script doesn't sign in. {private}") from e
        raise FeedError(f"GitHub returned HTTP {e.code} for {url}") from e
    except (OSError, json.JSONDecodeError) as e:  # URLError, timeouts and resets are OSErrors
        raise FeedError(f"Couldn't read {url}: {e}") from e


def download(url: str, dest: Path, hosts) -> None:
    if not trusted_download(url, hosts):
        raise FeedError(f"Refusing to download from {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
            if not trusted_download(response.geturl(), hosts):
                raise FeedError(f"{url} redirected to an unexpected host: {response.geturl()}")
            with dest.open("wb") as out:
                shutil.copyfileobj(response, out, CHUNK)
    except OSError as e:  # URLError, timeouts, resets and disk errors
        raise FeedError(f"Couldn't download {url}: {e}") from e


def release_from_github(tag: str, staging: Path, repo: str, channel: str | None, notes: str | None,
                        published: str | None, server_url: str = project.SERVER_URL) -> Release:
    """Download a GitHub release's installers and SHA256SUMS.txt into `staging`."""
    try:
        project.check_repo(repo)
        api_root = f"{project.api_url(server_url)}/repos/"
        hosts = project.download_hosts(server_url)
    except ValueError as e:
        raise FeedError(str(e)) from e
    if tag == "latest":
        url = f"{api_root}{repo}/releases/latest"
    elif match := TAG_RE.fullmatch(tag):
        url = f"{api_root}{repo}/releases/tags/v{match.group(1)}"
    else:
        raise FeedError(f'{tag!r} isn\'t "latest" or a release tag like v0.6.0 or v0.6.0-preview.1')
    data = fetch_json(url, api_root)
    if data.get("draft"):
        raise FeedError(f"{data.get('tag_name')} is a draft; publish it first")
    tag_match = TAG_RE.fullmatch(str(data.get("tag_name", "")))
    if not tag_match:
        raise FeedError(f"GitHub returned an unrecognised tag {data.get('tag_name')!r}")
    version = tag_match.group(1)
    urls = {str(a.get("name", "")): str(a.get("browser_download_url", "")) for a in data.get("assets") or []}
    installers = {platform: name for platform, template in INSTALLER_NAMES.items()
                  if (name := template.format(version=version)) in urls}
    if not installers:
        raise FeedError(f"The {version} release has no SnipForge-{version}-setup.exe")
    if SUMS_FILE not in urls:
        raise FeedError(f"The {version} release has no {SUMS_FILE}")
    for name in [SUMS_FILE, *installers.values()]:  # check every link before downloading anything
        if not trusted_download(urls[name], hosts):
            raise FeedError(f"{name} on the {version} release points somewhere unexpected: {urls[name]}")

    download(urls[SUMS_FILE], staging / SUMS_FILE, hosts)
    sums = parse_sums((staging / SUMS_FILE).read_text(encoding="utf-8-sig"))
    assets = []
    for platform, name in installers.items():
        if name not in sums:
            raise FeedError(f"{name} isn't listed in the release's {SUMS_FILE}")
        download(urls[name], staging / name, hosts)
        assets.append(Asset(platform, name, staging / name, sums[name]))
    default_channel = "preview" if data.get("prerelease") else channel_for(version)
    body = str(data.get("body") or "").replace("\r\n", "\n").strip()
    return Release(version, resolve_channel(version, channel, default_channel),
                   published or str(data.get("published_at") or "") or None,
                   body if notes is None else notes, tuple(assets))


# ------------------------------------------------------------------ publishing


@dataclass(frozen=True)
class Outcome:
    copied: tuple[str, ...]
    unchanged: tuple[str, ...]
    action: str   # "added", "updated" or "unchanged"


def preflight(release: Release, target: Path, existing: dict | None, replace: bool) -> None:
    """Fail before touching the target if any installer is wrong or would silently change."""
    for asset in release.assets:
        actual = sha256_file(asset.source)
        if actual != asset.sha256:
            raise FeedError(f"SHA-256 of {asset.source} is {actual}, but {SUMS_FILE} says {asset.sha256}")
        if replace:
            continue
        dest = target / asset.name
        listed = {a["name"]: a["sha256"].lower() for a in (existing or {}).get("assets", [])}
        on_disk = sha256_file(dest) if dest.is_file() else None
        for current in (listed.get(asset.name), on_disk):
            if current and current != asset.sha256:
                raise FeedError(f"The feed already has a different {asset.name} (sha256 {current}). "
                                "Use --replace if this rebuild should replace it.")


def copy_asset(asset: Asset, target: Path) -> bool:
    """Copy one installer into the feed and verify the copy. False if it was already there."""
    dest = target / asset.name
    if dest.is_file() and sha256_file(dest) == asset.sha256:
        return False
    tmp = target / f".{asset.name}.tmp"
    try:
        shutil.copyfile(asset.source, tmp)
        actual = sha256_file(tmp)
        if actual != asset.sha256:
            raise FeedError(f"The copy of {asset.name} in {target} has SHA-256 {actual}, expected {asset.sha256}")
        replace_file(tmp, dest)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    return True


def build_entry(release: Release, existing: dict | None, now: datetime) -> dict:
    if release.published is not None:
        published = iso_utc(parse_timestamp(release.published))
    else:
        published = existing["published"] if existing else iso_utc(now)
    notes = release.notes if release.notes is not None else (existing["notes"] if existing else "")
    entry = {
        "version": release.version,
        "channel": release.channel,
        "published": published,
        "notes": notes,
        "assets": [{"platform": a.platform, "name": a.name, "sha256": a.sha256} for a in release.assets],
    }
    validate_entry(entry)
    return entry


def publish(release: Release, target: Path, replace: bool = False, now: datetime | None = None) -> Outcome:
    feed_path = target / FEED_FILE
    feed = load_feed(feed_path)
    existing = next((r for r in feed["releases"] if r["version"] == release.version), None)
    entry = build_entry(release, existing, now or datetime.now(UTC))
    preflight(release, target, existing, replace)

    target.mkdir(parents=True, exist_ok=True)
    copied, unchanged = [], []
    for asset in release.assets:
        (copied if copy_asset(asset, target) else unchanged).append(asset.name)
    # The installers are in place and verified; only now does the feed point at them.
    changed = write_json_atomic(feed_path, merge_entry(feed, entry))
    action = "unchanged" if not changed else ("updated" if existing else "added")
    return Outcome(tuple(copied), tuple(unchanged), action)


# ------------------------------------------------------------------ command line


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--dist", type=Path, help="build output folder with SnipForge-<version>-setup.exe "
                                                   "and SHA256SUMS.txt (from scripts/build.py --installer)")
    source.add_argument("--github", metavar="TAG", help='GitHub release to mirror: "latest" (newest stable) '
                                                        "or a tag such as v0.6.0 or v0.6.0-preview.1")
    parser.add_argument("--target", type=Path, required=True,
                        help="feed folder: a synced SharePoint library, a network share or any folder")
    parser.add_argument("--version", help="with --dist: which version to publish if the folder has several")
    parser.add_argument("--channel", choices=CHANNELS, help="default: preview for GitHub prereleases and "
                                                            "x.y.z-preview.N versions, otherwise stable")
    notes = parser.add_mutually_exclusive_group()
    notes.add_argument("--notes", help="release notes (default: the GitHub release notes, or keep the feed's)")
    notes.add_argument("--notes-file", type=Path, help="read the release notes from a UTF-8 text file")
    parser.add_argument("--published", help="ISO-8601 publish time (default: GitHub's, or now for a new "
                                            "--dist entry)")
    parser.add_argument("--replace", action="store_true",
                        help="let a rebuilt installer replace a different one already in the feed")
    parser.add_argument("--repo", default=project.REPO,
                        help=f"with --github: the repository (default {project.REPO}, from snipforge/project.py)")
    parser.add_argument("--server-url", default=project.SERVER_URL,
                        help=f"with --github: the GitHub server (default {project.SERVER_URL}); in Actions, "
                             "pass $GITHUB_SERVER_URL")
    args = parser.parse_args(argv)
    if args.version and args.github:
        parser.error("--version only applies to --dist; --github takes the version from the tag")
    return args


def read_notes(args: argparse.Namespace) -> str | None:
    if args.notes_file is None:
        return args.notes
    try:
        return args.notes_file.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()
    except (OSError, UnicodeDecodeError) as e:
        raise FeedError(f"Couldn't read {args.notes_file}: {e}") from e


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        notes = read_notes(args)
        with tempfile.TemporaryDirectory(prefix="snipforge-feed-") as staging:
            if args.github:
                release = release_from_github(args.github, Path(staging), args.repo, args.channel, notes,
                                              args.published, args.server_url)
            else:
                release = release_from_dist(args.dist, args.version, args.channel, notes, args.published)
            outcome = publish(release, args.target, replace=args.replace)
    except FeedError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except OSError as e:  # unreachable share, no permission, disk full...
        print(f"error: {e}. Nothing was added to {FEED_FILE}.", file=sys.stderr)
        return 1
    for name in outcome.copied:
        print(f"Copied {name}")
    for name in outcome.unchanged:
        print(f"Already in the feed: {name}")
    print(f"{FEED_FILE}: {outcome.action} {release.version} ({release.channel}) in {args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
