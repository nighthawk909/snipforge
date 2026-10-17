"""What the update engine remembers between runs: the verified installer that's waiting to be
installed (READY_KEY + the file in the download folder) and the release being installed
(INSTALLED_KEY), so the next start can show what changed or report an update that didn't finish.

Settings and %TEMP% are writable by the user, so everything read back is checked again: the
installer must still sit in our download folder and still match the SHA-256 recorded when it was
verified.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from pathlib import Path

from .update_prefs import INSTALLED_KEY, READY_KEY, load_record, save_record
from .updates import SHA256_RE, Release, release_from_dict, release_to_dict, sha256_file, version_key

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Ready:
    release: Release
    path: Path
    sha256: str


@dataclass(frozen=True)
class InstallRecord:
    release: Release                    # what was being installed
    previous: str | None                # the version that started the install
    pin_before: str | None              # pinned_version before the install (to undo a failed rollback)
    pin_after: str | None
    started: dt.datetime | None


class DownloadStore:
    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.ready: Ready | None = None

    # ------------------------------------------------------------------ the waiting installer
    def keep(self, release: Release, path: Path, sha256: str | None = None) -> Ready:
        """Remember a freshly verified download (replacing any other). `sha256` is the digest the
        source verified; without it the file is hashed (raises OSError if unreadable)."""
        path = Path(path)
        digest = sha256.lower() if sha256 and SHA256_RE.fullmatch(sha256) else sha256_file(path)
        if self.ready is not None and self.ready.path != path:
            self.discard()
        self.ready = Ready(release, path, digest)
        save_record(READY_KEY, {"release": release_to_dict(release), "path": str(path), "sha256": digest})
        return self.ready

    def is_ready(self, release: Release) -> bool:
        """Whether this release is the one downloaded (a feed that re-publishes a version with a new
        hash needs a fresh download)."""
        ready = self.ready
        if ready is None or version_key(ready.release.version) != version_key(release.version):
            return False
        return not (release.sha256 and ready.release.sha256) or release.sha256 == ready.release.sha256

    def verified(self, ready: Ready) -> bool:
        try:
            return sha256_file(ready.path) == ready.sha256
        except OSError:
            return False

    def discard(self) -> None:
        ready, self.ready = self.ready, None
        save_record(READY_KEY, None)
        if ready is not None:
            self._remove(ready.path)

    def restore(self) -> Ready | None:
        """The download saved by the last run, if it's still in our folder and unchanged."""
        record = load_record(READY_KEY)
        ready = self._from_record(record) if record is not None else None
        if ready is None:
            save_record(READY_KEY, None)
        self.ready = ready
        return ready

    def _from_record(self, record: dict) -> Ready | None:
        release, path, digest = release_from_dict(record.get("release")), record.get("path"), record.get("sha256")
        if release is None or not isinstance(path, str) or not isinstance(digest, str):
            return None
        if not SHA256_RE.fullmatch(digest):
            return None
        ready = Ready(release, Path(path), digest.lower())
        return ready if self._inside(ready.path) and self.verified(ready) else None

    def clean(self) -> None:
        """Remove every downloaded installer (after an update finished or failed)."""
        save_record(READY_KEY, None)
        self.ready = None
        if self.folder.is_dir():
            for path in self.folder.iterdir():
                if path.is_file():
                    self._remove(path)

    # ------------------------------------------------------------------ the install in progress
    @staticmethod
    def remember_install(release: Release, previous: str, pin_before: str | None, pin_after: str | None,
                         started: dt.datetime) -> None:
        save_record(INSTALLED_KEY, {"release": release_to_dict(release), "previous": previous,
                                    "pin_before": pin_before, "pin_after": pin_after,
                                    "started": started.isoformat(timespec="seconds")})

    @staticmethod
    def forget_install() -> None:
        save_record(INSTALLED_KEY, None)

    @staticmethod
    def load_installed() -> InstallRecord | None:
        """What a run started installing, if anything (forget_install() once it's dealt with)."""
        record = load_record(INSTALLED_KEY)
        release = release_from_dict(record.get("release")) if record is not None else None
        if release is None:
            if record is not None:
                save_record(INSTALLED_KEY, None)
            return None

        def text(key):
            value = record.get(key)
            return value if isinstance(value, str) and value else None

        try:
            started = dt.datetime.fromisoformat(text("started")) if text("started") else None
        except ValueError:
            started = None
        return InstallRecord(release, text("previous"), text("pin_before"), text("pin_after"), started)

    # ------------------------------------------------------------------ files
    def _inside(self, path: Path) -> bool:
        try:
            return path.resolve().parent == self.folder.resolve()
        except OSError:
            return False

    def _remove(self, path: Path) -> None:
        if not self._inside(path):
            return  # only ever delete our own downloads
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            log.info("Couldn't remove %s: %s", path, exc)
