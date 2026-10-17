"""Where releases come from: GitHub Releases (the default, in the repository snipforge/project.py
names) or a feed IT points SnipForge at.

A source lists releases (fetch) and downloads one release's installer, checks its SHA-256 and
saves it (download). Results arrive on the main thread through callbacks:
fetch -> done(list[Release] | None, error); download -> done(Path | None, error, verified sha256).

A feed is a folder (UNC share, or a SharePoint library synced by OneDrive) or an https:// address
holding releases.json plus the installers it lists:

    {"schema": 1, "releases": [{"version": "0.6.0", "channel": "stable"|"preview",
      "published": "2026-10-20T15:00:00Z", "notes": "...",
      "assets": [{"platform": "windows", "name": "SnipForge-0.6.0-setup.exe", "sha256": "<64 hex>"}]}]}
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

from . import project
from .update_platform import PLATFORM, installer_suffix_ok
from .update_policy import PolicyInfo, feed_location
from .update_transport import CANCELED, NEEDS_SIGN_IN, NOT_FOUND, FileTransport, NetworkTransport
from .updates import (
    SHA256_RE,
    Release,
    expected_hash,
    normalize_version,
    parse_releases,
    parse_time,
    sha256_bytes,
    sort_newest_first,
    trim_notes,
    trusted_download,
    version_key,
)

log = logging.getLogger(__name__)

MANIFEST_NAME = "releases.json"
MANIFEST_SCHEMA = 1
MAX_FEED_RELEASES = 200
MAX_LIST_BYTES = 2_000_000
MAX_SUMS_BYTES = 64_000
MAX_INSTALLER_BYTES = 500_000_000
CHECK_TIMEOUT_MS = 8_000
FEED_TIMEOUT_MS = 30_000        # file shares can be slow to wake up
DOWNLOAD_TIMEOUT_MS = 300_000
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,150}")
UNEXPECTED = "The download came from an unexpected location."
NO_CHECKSUM = "The release has no checksum for the installer."

FetchDone = Callable[[list[Release] | None, str], None]
DownloadDone = Callable[[Path | None, str, str], None]


def save_verified(data: bytes, expected: str | None, folder: Path, name: str) -> tuple[Path | None, str, str]:
    """Save the installer only if its SHA-256 matches; never leave a half-written file behind.
    Returns (path, "", sha256) or (None, error, "")."""
    if not SAFE_NAME.fullmatch(name or ""):
        return None, "The installer has an unexpected name.", ""
    if not expected:
        return None, NO_CHECKSUM, ""
    digest = sha256_bytes(data)
    if digest != expected.lower():
        return None, "The download didn't match its checksum, so it wasn't installed.", ""
    path, partial = folder / name, folder / f"{name}.part"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        partial.write_bytes(data)
        os.replace(partial, path)
    except OSError as exc:
        return None, f"Couldn't save the update: {exc}", ""
    return path, "", digest


class _Chain:
    """A download made of several requests; cancel() stops whichever one is running."""

    def __init__(self) -> None:
        self.cancelled = False
        self.current = None

    def cancel(self) -> None:
        self.cancelled = True
        if self.current is not None:
            self.current.cancel()


class UpdateSource:
    """Base class. `label` names the source in messages ("GitHub", "your organization's ...")."""

    label = "the update source"

    def fetch(self, done: FetchDone):
        raise NotImplementedError

    def download(self, release: Release, folder: Path, done: DownloadDone, progress=None):
        raise NotImplementedError


class GitHubSource(UpdateSource):
    label = "GitHub"

    def __init__(self, repo: str = project.REPO, server_url: str = project.SERVER_URL, api_base: str | None = None,
                 transport=None) -> None:
        api_base = api_base or project.api_url(server_url)
        if urlparse(api_base).scheme != "https":
            raise ValueError("The GitHub API address must use https.")
        self._repo, self._server = repo, server_url
        self._url = f"{api_base.rstrip('/')}/repos/{repo}/releases?per_page=20"
        self._releases = f"{project.repo_url(server_url, repo)}/releases"
        self._assets = f"{self._releases}/download/"
        self._hosts = project.download_hosts(server_url)
        self._transport = transport

    def _cant_see(self) -> str:
        """A private or internal repository (GitHub Enterprise) answers anonymous requests with a
        sign-in, "not found" or a sign-in page; point people at their organization's own copy."""
        return (f"SnipForge can't see the releases at {self._releases}: the repository is private, needs a "
                "sign-in (which SnipForge doesn't do) or has moved. If your organization keeps SnipForge "
                "updates in a shared folder, ask your IT team to point SnipForge at it (the UpdateFeed policy).")

    @property
    def transport(self):
        if self._transport is None:
            self._transport = NetworkTransport()
        return self._transport

    def fetch(self, done: FetchDone):
        def got(data, error, final):
            if data is None:
                done(None, self._cant_see() if error in (NEEDS_SIGN_IN, NOT_FOUND) else error)
                return
            if urlparse(final).hostname != urlparse(self._url).hostname:  # usually a sign-in page
                log.info("The release list came from %s instead of GitHub", urlparse(final).hostname)
                done(None, self._cant_see())
                return
            try:
                releases = parse_releases(json.loads(data.decode("utf-8")), self._repo, server_url=self._server)
            except ValueError as exc:
                done(None, f"Unexpected response from GitHub ({exc}).")
                return
            done(releases, "")

        return self.transport.get(self._url, timeout_ms=CHECK_TIMEOUT_MS, max_bytes=MAX_LIST_BYTES, done=got,
                                  accept="application/vnd.github+json")

    def download(self, release: Release, folder: Path, done: DownloadDone, progress=None):
        chain = _Chain()
        name, installer, sums = release.installer_name, release.installer_url, release.sums_url
        if not (name and installer and sums):
            done(None, "This release has no installer to download.", "")
            return chain
        if not all(url.startswith(self._assets) and trusted_download(url, self._hosts) for url in (installer, sums)):
            done(None, UNEXPECTED, "")
            return chain

        def got_installer(expected: str):
            def handler(data, error, final):
                if data is None:
                    done(None, error, "")
                elif not trusted_download(final, self._hosts):
                    done(None, UNEXPECTED, "")
                else:
                    done(*save_verified(data, expected, folder, name))
            return handler

        def got_sums(data, error, final):
            if data is None:
                done(None, error, "")
                return
            if not trusted_download(final, self._hosts):
                done(None, UNEXPECTED, "")
                return
            expected = expected_hash(data.decode("utf-8", errors="replace"), name)
            if expected is None:
                done(None, NO_CHECKSUM, "")
            elif chain.cancelled:
                done(None, CANCELED, "")
            else:
                chain.current = self.transport.get(installer, timeout_ms=DOWNLOAD_TIMEOUT_MS,
                                                   max_bytes=MAX_INSTALLER_BYTES, done=got_installer(expected),
                                                   progress=progress)

        first = self.transport.get(sums, timeout_ms=DOWNLOAD_TIMEOUT_MS, max_bytes=MAX_SUMS_BYTES, done=got_sums)
        if chain.current is None:  # a synchronous transport may already have moved on to the installer
            chain.current = first
        return chain


def _is_url(location: str) -> bool:
    return location.lower().startswith("https://")


class FolderFeedSource(UpdateSource):
    label = "your organization's update location"

    def __init__(self, location: str, transport=None) -> None:
        normalized = feed_location(location)
        if normalized is None:
            raise ValueError(f"Not a usable update location: {location!r}")
        self.location = normalized
        if _is_url(normalized):
            if urlparse(normalized).path.lower().endswith(".json"):
                self._manifest, self._base = normalized, urljoin(normalized, ".")
            else:
                self._base = normalized if normalized.endswith("/") else normalized + "/"
                self._manifest = urljoin(self._base, MANIFEST_NAME)
        else:
            path = Path(normalized)
            if path.suffix.lower() == ".json":
                self._manifest, self._base = str(path), str(path.parent)
            else:
                self._manifest, self._base = str(path / MANIFEST_NAME), str(path)
        self._transport = transport

    @property
    def transport(self):
        if self._transport is None:
            self._transport = NetworkTransport() if _is_url(self.location) else FileTransport()
        return self._transport

    def _secure(self, final: str) -> bool:
        return not _is_url(self.location) or urlparse(final).scheme == "https"

    def _fetch_error(self, error: str) -> str:
        if error == NEEDS_SIGN_IN:
            return (f"{self.location} wants a sign-in, which SnipForge doesn't do. Ask your IT team to use a "
                    "synced folder or a file share for updates instead.")
        if error == NOT_FOUND:
            return f"There's no {MANIFEST_NAME} at {self._manifest}."
        return error

    def fetch(self, done: FetchDone):
        def got(data, error, final):
            if data is None:
                done(None, self._fetch_error(error))
                return
            if not self._secure(final):
                done(None, UNEXPECTED)
                return
            try:
                releases = parse_manifest(data, self._base)
            except ValueError as exc:
                done(None, f"The update list at {self.location} couldn't be used: {exc}.")
                return
            done(releases, "")

        return self.transport.get(self._manifest, timeout_ms=FEED_TIMEOUT_MS, max_bytes=MAX_LIST_BYTES, done=got,
                                  accept="application/json")

    def _inside_feed(self, location: str, name: str) -> bool:
        if not SAFE_NAME.fullmatch(name or ""):
            return False
        if _is_url(self.location):
            return location == urljoin(self._base, quote(name))
        expected = os.path.join(self._base, name)
        return os.path.normcase(os.path.normpath(location)) == os.path.normcase(os.path.normpath(expected))

    def download(self, release: Release, folder: Path, done: DownloadDone, progress=None):
        location, name = release.installer_url, release.installer_name
        if not (location and name):
            done(None, "This release has no installer for this PC.", "")
            return _Chain()
        if not release.sha256:
            done(None, NO_CHECKSUM, "")
            return _Chain()
        if not self._inside_feed(location, name):
            done(None, UNEXPECTED, "")
            return _Chain()

        def got(data, error, final):
            if data is None:
                done(None, error, "")
            elif not self._secure(final):
                done(None, UNEXPECTED, "")
            else:
                done(*save_verified(data, release.sha256, folder, name))

        return self.transport.get(location, timeout_ms=DOWNLOAD_TIMEOUT_MS, max_bytes=MAX_INSTALLER_BYTES,
                                  done=got, progress=progress)


class UnavailableSource(UpdateSource):
    """Stands in when policy names a feed SnipForge can't use, so checks fail instead of using GitHub."""

    def __init__(self, message: str) -> None:
        self.message = message
        self.label = "your organization's update location"

    def fetch(self, done: FetchDone):
        done(None, self.message)
        return _Chain()

    def download(self, release: Release, folder: Path, done: DownloadDone, progress=None):
        done(None, self.message, "")
        return _Chain()


def source_for(policy: PolicyInfo) -> UpdateSource:
    if policy.source != "feed":
        return GitHubSource()
    if policy.source_problem:
        return UnavailableSource(policy.source_problem)
    return FolderFeedSource(policy.feed)


# --- releases.json --------------------------------------------------------------------------
def parse_manifest(data, base: str, platform: str = PLATFORM) -> list[Release]:
    """Releases for this platform, newest first. Raises ValueError if the file itself is unusable;
    individual bad entries are skipped (and logged) so one typo doesn't stop every update."""
    if isinstance(data, (bytes, bytearray)):
        try:
            data = json.loads(bytes(data).decode("utf-8-sig"))
        except ValueError as exc:
            raise ValueError(f"it isn't valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError("it isn't a SnipForge releases.json file")
    schema = data.get("schema")
    if type(schema) is not int:
        raise ValueError("it has no schema number")
    if schema != MANIFEST_SCHEMA:
        raise ValueError(f"it uses format {schema}, which this version of SnipForge doesn't understand")
    entries = data.get("releases")
    if not isinstance(entries, list):
        raise ValueError("its releases aren't a list")
    if _is_url(base) and not base.endswith("/"):
        base += "/"
    entries = entries[:MAX_FEED_RELEASES]
    found: dict[tuple, Release] = {}
    invalid = 0
    for item in entries:
        try:
            release = _feed_release(item, base, platform)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            invalid += 1
            log.warning("Skipping a release in the update feed: %s", exc)
            continue
        if release is not None:
            found.setdefault(version_key(release.version), release)
    if entries and invalid == len(entries):
        raise ValueError("none of its releases are valid")
    return sort_newest_first(found.values())


def _feed_release(item, base: str, platform: str) -> Release | None:
    if not isinstance(item, dict):
        raise ValueError("a release entry isn't an object")
    version = normalize_version(item["version"]) if isinstance(item.get("version"), str) else None
    if not version:
        raise ValueError(f"unrecognised version {item.get('version')!r}")
    channel = item.get("channel")
    if channel not in ("stable", "preview"):
        raise ValueError(f"{version}: channel must be stable or preview, not {channel!r}")
    assets = item.get("assets")
    if not isinstance(assets, list):
        raise ValueError(f"{version}: assets aren't a list")
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("platform") != platform:
            continue
        name, digest = asset.get("name"), asset.get("sha256")
        if not isinstance(name, str) or not SAFE_NAME.fullmatch(name) or not installer_suffix_ok(name, platform):
            raise ValueError(f"{version}: unexpected installer name {name!r}")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise ValueError(f"{version}: sha256 must be 64 hex characters")
        location = urljoin(base, quote(name)) if _is_url(base) else str(Path(base) / name)
        notes = item.get("notes")
        return Release(version, "", trim_notes(notes) if isinstance(notes, str) else "", name, location, None,
                       prerelease=channel == "preview", published=parse_time(item.get("published")),
                       sha256=digest.lower())
    return None
