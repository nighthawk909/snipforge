"""Release data shared by every update source: versions, GitHub release parsing and checksums.

SnipForge checks for updates once a day unless updates are set to Manual or turned off by policy
(see snipforge/update_policy.py and docs/IT-REVIEW.md). Network access lives in update_sources.py.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

from . import REPO, __version__
from .update_platform import PLATFORM, installer_version

RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
TRUSTED_PREFIX = f"https://github.com/{REPO}/"
# Release downloads start at github.com and redirect to GitHub's asset storage.
DOWNLOAD_HOSTS = {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
NOTES_MAX = 1200
SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")
_VERSION_RE = re.compile(r"^\s*v?(\d+(?:\.\d+)*)(?:[-.]?([A-Za-z][0-9A-Za-z.-]*))?")


# --- versions ------------------------------------------------------------------------------
def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.3' -> (1, 2, 3); anything unparseable -> ()."""
    match = _VERSION_RE.match(text or "")
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()


def _split(text: str) -> tuple[tuple[int, ...], list[str]] | None:
    match = _VERSION_RE.match((text or "").split("+", 1)[0])
    if not match:
        return None
    numbers = tuple(int(part) for part in match.group(1).split("."))
    return numbers, re.findall(r"[A-Za-z]+|\d+", match.group(2) or "")


def normalize_version(text: str) -> str | None:
    """'v0.6.0-beta.1' -> '0.6.0-beta.1'; None if it isn't a version."""
    parts = _split(text)
    if parts is None:
        return None
    numbers, pre = parts
    core = ".".join(str(n) for n in numbers)
    return f"{core}-{'.'.join(pre)}" if pre else core


def version_key(text: str) -> tuple | None:
    """A sortable key: 0.6.0-beta.1 < 0.6.0-rc.1 < 0.6.0 < 0.6.1. None if it isn't a version."""
    parts = _split(text)
    if parts is None:
        return None
    numbers, pre = parts
    padded = numbers + (0,) * max(0, 4 - len(numbers))
    if not pre:
        return padded, (1,)
    return padded, (0, *((0, int(p), "") if p.isdigit() else (1, 0, p.lower()) for p in pre))


def compare_versions(a: str, b: str) -> int:
    """-1, 0 or 1. Raises ValueError if either isn't a version."""
    key_a, key_b = version_key(a), version_key(b)
    if key_a is None or key_b is None:
        raise ValueError(f"Not a version: {a if key_a is None else b!r}")
    return (key_a > key_b) - (key_a < key_b)


def is_newer(latest: str, current: str = __version__) -> bool:
    new, cur = version_key(latest), version_key(current)
    return new is not None and cur is not None and new > cur


def trusted_download(url: str) -> bool:
    parsed = urlparse(url or "")
    return parsed.scheme == "https" and parsed.hostname in DOWNLOAD_HOSTS


def trim_notes(text: str) -> str:
    notes = str(text or "").strip()
    if len(notes) > NOTES_MAX:
        notes = notes[:NOTES_MAX].rsplit("\n", 1)[0] + "\n…"
    return notes


# --- releases ------------------------------------------------------------------------------
@dataclass(frozen=True)
class Release:
    version: str                 # "0.5.0" or "0.6.0-beta.1"
    page: str                    # release page on github.com ("" for a feed)
    notes: str                   # release notes, plain text, trimmed; never render as HTML
    installer_name: str | None   # "SnipForge-0.5.0-setup.exe"
    installer_url: str | None    # https URL, or a file path inside a folder feed
    sums_url: str | None         # SHA256SUMS.txt (GitHub)
    prerelease: bool = False
    published: dt.datetime | None = None
    sha256: str | None = None    # expected installer hash when the source states it (feeds)

    @property
    def channel(self) -> str:
        return "preview" if self.prerelease else "stable"


def parse_time(text) -> dt.datetime | None:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        when = dt.datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=dt.UTC)


def parse_release(data: dict, repo: str = REPO, platform: str = PLATFORM) -> Release:
    """Validate one GitHub release object. Raises ValueError if it's unusable."""
    version = normalize_version(str(data["tag_name"]))
    if not version:
        raise ValueError("GitHub returned an unrecognised version")
    trusted = f"https://github.com/{repo}/"
    page = str(data.get("html_url") or "")
    if not page.startswith(trusted):  # never open a link we didn't expect
        page = f"{trusted}releases/latest"
    installer_name = installer_url = sums_url = None
    assets_prefix = f"{trusted}releases/download/"
    for asset in data.get("assets") or []:
        name, url = str(asset.get("name", "")), str(asset.get("browser_download_url", ""))
        if not url.startswith(assets_prefix) or not trusted_download(url):
            continue
        asset_version = installer_version(name, platform)
        if asset_version is not None and version_key(asset_version) == version_key(version):
            installer_name, installer_url = name, url
        elif name == "SHA256SUMS.txt":
            sums_url = url
    return Release(version, page, trim_notes(data.get("body")), installer_name, installer_url, sums_url,
                   prerelease=bool(data.get("prerelease")), published=parse_time(data.get("published_at")))


def parse_releases(data, repo: str = REPO, platform: str = PLATFORM) -> list[Release]:
    """A GitHub /releases list, newest first. Drafts and unrecognisable releases are left out."""
    if not isinstance(data, list):
        raise ValueError("GitHub didn't return a list of releases")
    found: dict[tuple, Release] = {}
    for item in data:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        try:
            release = parse_release(item, repo, platform)
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        found.setdefault(version_key(release.version), release)
    return sort_newest_first(found.values())


def sort_newest_first(releases) -> list[Release]:
    return sorted(releases, key=lambda r: version_key(r.version), reverse=True)


def filter_channel(releases, channel: str) -> list[Release]:
    """Stable shows only full releases; Preview shows prereleases too."""
    return [r for r in releases if channel == "preview" or not r.prerelease]


def is_skipped(release: Release, prefs) -> bool:
    """Skipping a version also quiets anything older than it; newer releases are offered again."""
    skip = prefs.skip_version
    return bool(skip) and version_key(release.version) <= version_key(skip)


def pick_update(releases, current: str, prefs) -> Release | None:
    """The newest release in the channel that's newer than `current` and not past the pinned version."""
    current_key, pin_key = version_key(current), version_key(prefs.pinned_version or "")
    for release in sort_newest_first(filter_channel(releases, prefs.channel)):
        key = version_key(release.version)
        if current_key is not None and key <= current_key:
            return None
        if pin_key is None or key <= pin_key:
            return release
    return None


# --- checksums -----------------------------------------------------------------------------
def expected_hash(sums_text: str, filename: str) -> str | None:
    for line in sums_text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*") == filename and SHA256_RE.fullmatch(parts[0]):
            return parts[0].lower()
    return None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


# --- saving a release in settings ----------------------------------------------------------
def release_to_dict(release: Release) -> dict:
    data = asdict(release)
    data["published"] = release.published.isoformat() if release.published else None
    return data


def release_from_dict(data) -> Release | None:
    """The inverse of release_to_dict; None if the data is damaged (settings can be edited by hand)."""
    if not isinstance(data, dict) or not normalize_version(str(data.get("version", ""))):
        return None
    try:
        text = {k: data.get(k) for k in ("page", "notes", "installer_name", "installer_url", "sums_url", "sha256")}
        if any(v is not None and not isinstance(v, str) for v in text.values()):
            return None
        if text["sha256"] is not None and not SHA256_RE.fullmatch(text["sha256"]):
            return None
        return Release(normalize_version(str(data["version"])), text["page"] or "", text["notes"] or "",
                       text["installer_name"], text["installer_url"], text["sums_url"],
                       prerelease=bool(data.get("prerelease")), published=parse_time(data.get("published")),
                       sha256=text["sha256"])
    except (TypeError, ValueError):
        return None


def disabled_by_policy() -> bool:
    from . import update_policy  # imported here: update_policy depends on this module

    return update_policy.read_policy().disabled
