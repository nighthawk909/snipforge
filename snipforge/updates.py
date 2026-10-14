"""Manual update check against GitHub Releases.

SnipForge makes no network calls on its own: this only runs when you click
"Check for updates" in the tray menu.
"""
from __future__ import annotations

import json
import re

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import REPO, __version__

RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
TRUSTED_PREFIX = f"https://github.com/{REPO}/"
TIMEOUT_MS = 8000


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.3' -> (1, 2, 3); anything unparseable -> ()."""
    match = re.match(r"^\s*v?(\d+(?:\.\d+)*)", text or "")
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()


def is_newer(latest: str, current: str = __version__) -> bool:
    new, cur = parse_version(latest), parse_version(current)
    return bool(new) and new > cur


class UpdateChecker(QObject):
    # status: "newer" | "current" | "error"; then version (or error text) and the download page
    finished = Signal(str, str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._network = QNetworkAccessManager(self)

    def check(self) -> None:
        request = QNetworkRequest(QUrl(RELEASES_API))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"User-Agent", f"SnipForge/{__version__}".encode())
        request.setTransferTimeout(TIMEOUT_MS)
        reply = self._network.get(request)
        reply.finished.connect(lambda: self._done(reply))

    def _done(self, reply: QNetworkReply) -> None:
        reply.deleteLater()
        if reply.error() != QNetworkReply.NoError:
            self.finished.emit("error", reply.errorString(), RELEASES_PAGE)
            return
        try:
            data = json.loads(bytes(reply.readAll()).decode("utf-8"))
            tag, page = str(data["tag_name"]), str(data.get("html_url") or RELEASES_PAGE)
        except (ValueError, KeyError, TypeError) as exc:
            self.finished.emit("error", f"Unexpected response from GitHub ({exc})", RELEASES_PAGE)
            return
        version = ".".join(str(n) for n in parse_version(tag))
        if not version:
            self.finished.emit("error", "GitHub returned an unrecognised version", RELEASES_PAGE)
            return
        if not page.startswith(TRUSTED_PREFIX):  # never open a link we didn't expect
            page = RELEASES_PAGE
        self.finished.emit("newer" if is_newer(tag) else "current", version, page)
