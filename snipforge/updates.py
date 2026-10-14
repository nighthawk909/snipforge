"""Update checks and downloads from GitHub Releases.

SnipForge checks for a newer release once a day (and when you click "Check for updates").
Organizations can turn checks off with the policy value
HKLM\\Software\\Policies\\SnipForge\\DisableUpdateCheck = 1 (DWORD).
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import winreg
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import REPO, __version__

RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
TRUSTED_PREFIX = f"https://github.com/{REPO}/"
# Release downloads start at github.com and redirect to GitHub's asset storage.
DOWNLOAD_HOSTS = {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
CHECK_TIMEOUT_MS = 8000
DOWNLOAD_TIMEOUT_MS = 300_000
POLICY_KEY = r"Software\Policies\SnipForge"
NOTES_MAX = 1200


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.3' -> (1, 2, 3); anything unparseable -> ()."""
    match = re.match(r"^\s*v?(\d+(?:\.\d+)*)", text or "")
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()


def is_newer(latest: str, current: str = __version__) -> bool:
    new, cur = parse_version(latest), parse_version(current)
    return bool(new) and new > cur


def trusted_download(url: str) -> bool:
    parsed = urlparse(url or "")
    return parsed.scheme == "https" and parsed.hostname in DOWNLOAD_HOSTS


@dataclass(frozen=True)
class Release:
    version: str                 # "0.5.0"
    page: str                    # release page on github.com
    notes: str                   # release notes, plain text, trimmed
    installer_name: str | None   # "SnipForge-0.5.0-setup.exe"
    installer_url: str | None
    sums_url: str | None         # SHA256SUMS.txt


def parse_release(data: dict) -> Release:
    """Validate a GitHub 'latest release' response. Raises ValueError if it's unusable."""
    version = ".".join(str(n) for n in parse_version(str(data["tag_name"])))
    if not version:
        raise ValueError("GitHub returned an unrecognised version")
    page = str(data.get("html_url") or "")
    if not page.startswith(TRUSTED_PREFIX):  # never open a link we didn't expect
        page = RELEASES_PAGE
    installer_name = installer_url = sums_url = None
    expected = f"SnipForge-{version}-setup.exe"
    for asset in data.get("assets") or []:
        name, url = str(asset.get("name", "")), str(asset.get("browser_download_url", ""))
        if not trusted_download(url):
            continue
        if name == expected:
            installer_name, installer_url = name, url
        elif name == "SHA256SUMS.txt":
            sums_url = url
    notes = str(data.get("body") or "").strip()
    if len(notes) > NOTES_MAX:
        notes = notes[:NOTES_MAX].rsplit("\n", 1)[0] + "\n…"
    return Release(version, page, notes, installer_name, installer_url, sums_url)


def expected_hash(sums_text: str, filename: str) -> str | None:
    for line in sums_text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*") == filename and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    return None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def disabled_by_policy() -> bool:
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, POLICY_KEY) as key:
                if int(winreg.QueryValueEx(key, "DisableUpdateCheck")[0]) == 1:
                    return True
        except (OSError, ValueError):
            continue
    return False


def install_mode(executable: str | None = None, frozen: bool | None = None) -> str:
    """'per-user' (can update itself silently), 'all-users' (IT-managed) or 'source'."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if not frozen:
        return "source"
    exe = Path(executable or sys.executable).resolve()
    local_programs = Path.home() / "AppData" / "Local" / "Programs"
    return "per-user" if local_programs in exe.parents else "all-users"


def _request(url: str, timeout_ms: int) -> QNetworkRequest:
    request = QNetworkRequest(QUrl(url))
    request.setRawHeader(b"User-Agent", f"SnipForge/{__version__}".encode())
    request.setTransferTimeout(timeout_ms)
    request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.NoLessSafeRedirectPolicy)
    return request


class UpdateChecker(QObject):
    # ("newer" | "current", Release) or ("error", message)
    finished = Signal(str, object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._network = QNetworkAccessManager(self)

    def check(self) -> None:
        request = _request(RELEASES_API, CHECK_TIMEOUT_MS)
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        reply = self._network.get(request)
        reply.finished.connect(lambda: self._done(reply))

    def _done(self, reply: QNetworkReply) -> None:
        reply.deleteLater()
        if reply.error() != QNetworkReply.NoError:
            self.finished.emit("error", reply.errorString())
            return
        try:
            release = parse_release(json.loads(bytes(reply.readAll()).decode("utf-8")))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            self.finished.emit("error", f"Unexpected response from GitHub ({exc})")
            return
        self.finished.emit("newer" if is_newer(release.version) else "current", release)


class UpdateDownloader(QObject):
    """Downloads a release's installer and SHA256SUMS.txt and verifies the checksum."""

    progress = Signal(int, int)       # bytes received, total
    finished = Signal(object, str)    # (Path to verified installer | None, error message)

    def __init__(self, folder: Path, parent=None) -> None:
        super().__init__(parent)
        self._folder = folder
        self._network = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._release: Release | None = None
        self._sums = ""

    def start(self, release: Release) -> None:
        if not (release.installer_url and release.sums_url and release.installer_name):
            self.finished.emit(None, "This release has no installer to download.")
            return
        self._release = release
        self._get(release.sums_url, self._got_sums)

    def cancel(self) -> None:
        if self._reply is not None:
            self._reply.abort()

    def _get(self, url: str, handler) -> None:
        self._reply = self._network.get(_request(url, DOWNLOAD_TIMEOUT_MS))
        reply = self._reply
        reply.downloadProgress.connect(self.progress)
        reply.finished.connect(lambda: handler(reply))

    def _ok(self, reply: QNetworkReply) -> bytes | None:
        reply.deleteLater()
        self._reply = None
        final_url = reply.url().toString()
        if reply.error() != QNetworkReply.NoError:
            self.finished.emit(None, reply.errorString())
            return None
        if not trusted_download(final_url):
            self.finished.emit(None, "The download came from an unexpected location.")
            return None
        return bytes(reply.readAll())

    def _got_sums(self, reply: QNetworkReply) -> None:
        data = self._ok(reply)
        if data is None:
            return
        self._sums = data.decode("utf-8", errors="replace")
        self._get(self._release.installer_url, self._got_installer)

    def _got_installer(self, reply: QNetworkReply) -> None:
        data = self._ok(reply)
        if data is None:
            return
        self.finished.emit(*self.verify_and_save(data))

    def verify_and_save(self, data: bytes) -> tuple[Path | None, str]:
        name = self._release.installer_name
        expected = expected_hash(self._sums, name)
        if expected is None:
            return None, "The release has no checksum for the installer."
        if sha256_bytes(data) != expected:
            return None, "The download didn't match its checksum, so it wasn't installed."
        try:
            self._folder.mkdir(parents=True, exist_ok=True)
            path = self._folder / name
            path.write_bytes(data)
        except OSError as exc:
            return None, f"Couldn't save the update: {exc}"
        return path, ""
