"""UpdateController: the update engine that the tray and the Settings window drive.

Listen to `state_changed(UpdateState)` and `releases_changed(list[Release])`; call check_now(),
download(release), install_ready(), install_version(version), skip(version), cancel() and
set_prefs(prefs). UpdateState.kind is one of:

  idle         nothing happening (also: updates turned off by policy, a check was canceled)
  checking     asking the update source for its releases
  available    a newer release isn't downloaded yet (Manual mode, IT-managed install, skipped, canceled)
  downloading  `progress` runs 0-99
  ready        a verified installer is waiting; install_ready() installs it
  installing   the installer is running and SnipForge is about to close
  current      nothing newer to install (after an update, `just_updated` has what changed)
  error        `message` says what went wrong, in words for users

Release notes are plain text from the network: show them with Qt.PlainText, never as HTML.
"""
from __future__ import annotations

import datetime as dt
import logging
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication

from . import __version__, update_platform, update_policy, update_sources
from .update_policy import MANAGED_MESSAGE, PolicyInfo
from .update_prefs import UpdatePrefs, load_last_checked, load_prefs, save_last_checked, save_prefs
from .update_store import DownloadStore, Ready
from .update_transport import CANCELED
from .updates import (
    Release,
    filter_channel,
    is_newer,
    is_skipped,
    normalize_version,
    pick_update,
    sort_newest_first,
    version_key,
)

log = logging.getLogger(__name__)

STATES = ("idle", "checking", "available", "downloading", "ready", "installing", "current", "error")
BUSY = ("checking", "downloading", "installing")
FIRST_CHECK_DELAY_MS = 60_000           # don't compete with startup
CHECK_EVERY_MS = 6 * 60 * 60 * 1000     # look every 6 hours; actually checks at most daily
MIN_HOURS_BETWEEN_CHECKS = 20
STARTUP_INSTALL_DELAY_MS = 5_000        # Auto mode: install a waiting update shortly after starting
QUIT_DELAY_MS = 500
RECENT_INSTALL = dt.timedelta(minutes=10)   # an installer started this recently may still be running
SESSION_END_GRACE_S = 120                   # after Windows says it's signing out (a sign-out can be vetoed)
DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "SnipForge" / "updates"


def check_is_due(now: dt.datetime, last: str | dt.datetime | None) -> bool:
    if isinstance(last, str):
        try:
            last = dt.datetime.fromisoformat(last)
        except ValueError:
            return True
    if last is None:
        return True
    try:
        return now - last >= dt.timedelta(hours=MIN_HOURS_BETWEEN_CHECKS)
    except TypeError:  # a hand-edited setting mixing time zones
        return True


@dataclass(frozen=True)
class UpdateState:
    kind: str = "idle"
    release: Release | None = None
    progress: int = 0                   # 0-100
    message: str = ""                   # for users; plain text
    last_checked: dt.datetime | None = None

    def __post_init__(self) -> None:
        if self.kind not in STATES:
            raise ValueError(f"Unknown update state {self.kind!r}")
        if not 0 <= self.progress <= 100:
            raise ValueError("progress must be 0-100")


class UpdateController(QObject):
    state_changed = Signal(object)      # UpdateState
    releases_changed = Signal(object)   # list[Release] in the current channel, newest first

    def __init__(self, parent=None, *, policy: PolicyInfo | None = None, source=None, download_dir=None,
                 current_version: str = __version__, install_mode: str | None = None, launcher=None,
                 quit_app=None, clock=None) -> None:
        super().__init__(parent)
        self._policy = policy if policy is not None else update_policy.read_policy()
        self._source = source if source is not None else update_sources.source_for(self._policy)
        self._store = DownloadStore(Path(download_dir or DOWNLOAD_DIR))
        self._current = current_version
        self._install_mode = install_mode or update_platform.install_mode()
        self._launcher = launcher or update_platform.launch_installer
        self._quit_app = quit_app or QCoreApplication.quit
        self._clock = clock or dt.datetime.now
        self._last_checked = load_last_checked()
        self._state = UpdateState(last_checked=self._last_checked)
        self._all: list[Release] = []
        self._just_updated: Release | None = None
        self._job = None
        self._serial = 0                # bumps whenever a job starts or is abandoned; stale callbacks are dropped
        self._install_after_download = False
        self._started = self._launched = False
        self._session_end_at: float | None = None
        self._timer = QTimer(self, interval=CHECK_EVERY_MS)
        self._timer.timeout.connect(self._automatic_check)
        self._first_check = QTimer(self, singleShot=True)
        self._first_check.timeout.connect(self._automatic_check)
        self._startup_install = QTimer(self, singleShot=True)
        self._startup_install.timeout.connect(self._install_at_start)

    # ------------------------------------------------------------------ what the UI reads
    @property
    def state(self) -> UpdateState:
        return self._state

    @property
    def current_version(self) -> str:
        """The version this copy of SnipForge is (what prompts call "you have")."""
        return self._current

    @property
    def releases(self) -> list[Release]:
        """The last fetched releases in the current channel, newest first (the version list)."""
        return sort_newest_first(filter_channel(self._all, self.prefs.channel))

    @property
    def prefs(self) -> UpdatePrefs:
        """The preferences in effect, with any values forced by policy (see `policy`)."""
        return self._policy.apply(load_prefs())

    @property
    def policy(self) -> PolicyInfo:
        return self._policy

    @property
    def install_mode(self) -> str:
        """'per-user' (installs updates itself), 'all-users' (IT updates it) or 'source'."""
        return self._install_mode

    @property
    def just_updated(self) -> Release | None:
        """The release this run was updated (or rolled back) to, so the UI can show what changed."""
        return self._just_updated

    @property
    def busy(self) -> bool:
        return self._state.kind in BUSY

    def install_blocker(self, release: Release) -> str:
        """Why this release can't be installed from inside SnipForge, or "" if it can."""
        if self._policy.disabled:
            return MANAGED_MESSAGE
        if self._install_mode == "all-users":
            return "SnipForge on this PC is installed for all users, so your IT team updates it."
        if self._install_mode != "per-user":
            return "This copy of SnipForge runs from source code, so it can't update itself."
        if not (release.installer_name and release.installer_url):
            return "This release has no installer for this PC."
        if not (release.sha256 or release.sums_url):
            return "This release has no checksum, so it can't be installed safely."
        if not self._policy.allow_rollback and version_key(release.version) < version_key(self._current):
            return "Your organization doesn't allow installing older versions."
        return ""

    def is_skipped(self, release: Release) -> bool:
        return is_skipped(release, self.prefs)

    # ------------------------------------------------------------------ actions
    def start(self) -> None:
        """Begin daily checks and pick up where the last run left off. Call once, after the tray is up."""
        if self._started:
            return
        self._started = True
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.install_on_quit)
            if isinstance(app, QGuiApplication):
                app.commitDataRequest.connect(self._on_session_end)
        installing = self._restore_installed()
        if self._policy.disabled:
            return
        if not installing:
            self._restore_ready()
        self._first_check.start(FIRST_CHECK_DELAY_MS)
        self._timer.start()

    def check_now(self) -> None:
        self._check(manual=True)

    def download(self, release: Release) -> None:
        if self._state.kind == "installing":
            return
        blocker = self.install_blocker(release)
        if blocker:
            self._set("error", release, blocker)
            return
        if self.busy:
            return
        if self._store.is_ready(release):
            self._set("ready", release, self._ready_message(release), 100)
            return
        self._start_download(release, install=False)

    def cancel(self) -> None:
        kind, release = self._state.kind, self._state.release
        if kind not in ("checking", "downloading"):
            return
        self._abandon_job()
        if kind == "downloading":
            self._set("available", release, "Download canceled.")
        else:
            self._set("idle", message="Check canceled.")

    def install_ready(self) -> None:
        """Install the downloaded, verified update now; SnipForge closes and the installer reopens it."""
        ready = self._store.ready
        if self._state.kind == "installing":
            return
        if ready is None:
            self._set("error", message="There's no downloaded update to install. Check for updates first.")
            return
        blocker = self.install_blocker(ready.release)
        if blocker:
            self._set("error", ready.release, blocker)
            return
        if self.busy:
            self._abandon_job()  # a check or download finishing now mustn't replace what we install
        if not self._store.verified(ready):
            self._store.discard()
            self._set("error", ready.release, "The downloaded update changed on disk, so it wasn't installed. "
                                              "Check for updates to download it again.")
            return
        error = self._launch(ready, relaunch=True)
        if error:
            self._set("error", ready.release, f"Couldn't start the update: {error}")
            return
        self._set("installing", ready.release,
                  f"Installing SnipForge {ready.release.version}… SnipForge will reopen in a moment.")
        QTimer.singleShot(QUIT_DELAY_MS, self._quit_app)

    def install_version(self, version: str) -> None:
        """Download and install a version from `releases`. An older one is a rollback and pins it."""
        if self._state.kind == "installing":
            return
        key = version_key(version or "")
        release = next((r for r in self.releases if version_key(r.version) == key), None) if key else None
        if release is None:
            self._set("error", message=f"SnipForge {version} isn't in the list of versions. "
                                       "Check for updates, then try again.")
            return
        if key == version_key(self._current):
            self._set("current", release, f"You're already on SnipForge {release.version}.")
            return
        blocker = self.install_blocker(release)
        if blocker:
            self._set("error", release, blocker)
            return
        if self.busy:
            self._abandon_job()
        if self._store.is_ready(release):
            self.install_ready()
        else:
            self._start_download(release, install=True)

    def skip(self, version: str) -> None:
        """Don't bring this version (or older) up again automatically; a newer release asks again."""
        normalized = normalize_version(version or "")
        if not normalized:
            raise ValueError(f"Not a version: {version!r}")
        prefs = replace(load_prefs(), skip_version=normalized)
        save_prefs(prefs)
        if self._state.kind == "installing":
            return  # never delete the installer that's running
        if self._store.ready is not None and is_skipped(self._store.ready.release, prefs):
            self._store.discard()
        release = self._state.release
        if release is not None and self._state.kind in ("available", "ready") and is_skipped(release, prefs):
            self._set("available", release, self._skipped_message(release))

    def set_prefs(self, prefs: UpdatePrefs) -> None:
        """Save the user's choices. Values forced by policy stay as they are."""
        if not isinstance(prefs, UpdatePrefs):
            raise TypeError("set_prefs needs an UpdatePrefs")
        before = self.prefs
        stored = load_prefs()
        save_prefs(replace(prefs, mode=stored.mode if self._policy.mode else prefs.mode,
                           channel=stored.channel if self._policy.channel else prefs.channel))
        if self._all and not self.busy:
            if self.prefs.channel != before.channel:
                self.releases_changed.emit(self.releases)
            self._evaluate(manual=False, allow_download=False)

    def install_on_quit(self) -> None:
        """Auto mode: install a waiting update as SnipForge quits, without reopening it."""
        ready = self._store.ready
        if self._launched or self._session_is_ending() or self._policy.disabled or ready is None or self.busy:
            return
        if self.prefs.mode != "auto" or self.install_blocker(ready.release) or not self._eligible(ready.release):
            return
        if self._store.verified(ready):
            error = self._launch(ready, relaunch=False)
            if error:
                log.warning("Couldn't install the update on quit: %s", error)

    # ------------------------------------------------------------------ checking
    def _automatic_check(self) -> None:
        if self._policy.disabled or self.busy or self.prefs.mode == "manual":
            return
        if check_is_due(self._clock(), self._last_checked):
            self._check(manual=False)

    def _check(self, manual: bool) -> None:
        if self._policy.disabled:
            self._set("idle", message=MANAGED_MESSAGE)
            return
        if self.busy:
            return
        serial = self._next_serial()
        self._set("checking", message="Checking for updates…")
        job = self._source.fetch(lambda releases, error: self._fetched(serial, manual, releases, error))
        if serial == self._serial and self._state.kind == "checking":
            self._job = job

    def _fetched(self, serial: int, manual: bool, releases, error: str) -> None:
        if serial != self._serial:
            return
        self._job = None
        if releases is None:
            log.info("Update check failed: %s", error)
            self._set("error", message=f"Couldn't check for updates: {error}")
            return
        self._last_checked = self._clock()
        save_last_checked(self._last_checked)
        self._all = list(releases)
        self.releases_changed.emit(self.releases)
        self._evaluate(manual)

    def _evaluate(self, manual: bool, allow_download: bool = True) -> None:
        if self._state.kind == "installing":
            return
        prefs = self.prefs
        candidate = pick_update(self._all, self._current, prefs)
        if self._store.ready is not None and not (candidate and self._store.is_ready(candidate)):
            self._store.discard()
        if candidate is None:
            self._set("current", message=self._current_message(prefs))
        elif self._store.ready is not None:
            self._set("ready", candidate, self._ready_message(candidate), 100)
        elif not manual and is_skipped(candidate, prefs):
            self._set("available", candidate, self._skipped_message(candidate))
        elif (blocker := self.install_blocker(candidate)) or prefs.mode == "manual" or not allow_download:
            self._set("available", candidate, blocker or f"SnipForge {candidate.version} is available.")
        else:
            self._start_download(candidate, install=False)

    # ------------------------------------------------------------------ downloading
    def _start_download(self, release: Release, install: bool) -> None:
        self._install_after_download = install
        serial = self._next_serial()
        self._set("downloading", release, f"Downloading SnipForge {release.version}…")
        job = self._source.download(release, self._store.folder,
                                    lambda path, error, sha256="": self._downloaded(serial, release, path,
                                                                                    error, sha256),
                                    lambda received, total: self._progress(serial, received, total))
        if serial == self._serial and self._state.kind == "downloading":
            self._job = job

    def _progress(self, serial: int, received: int, total: int) -> None:
        if serial != self._serial or self._state.kind != "downloading" or total <= 0:
            return
        percent = max(0, min(99, int(received * 100 / total)))
        if percent != self._state.progress:
            self._set("downloading", self._state.release, self._state.message, percent)

    def _downloaded(self, serial: int, release: Release, path, error: str, sha256: str = "") -> None:
        if serial != self._serial:
            return
        self._job = None
        install, self._install_after_download = self._install_after_download, False
        if path is None:
            if error == CANCELED:
                self._set("available", release, "Download canceled.")
            else:
                self._set("error", release, f"Couldn't download SnipForge {release.version}. {error}")
            return
        try:
            self._store.keep(release, path, sha256 or None)
        except OSError as exc:
            self._set("error", release, f"Couldn't read the downloaded update: {exc}")
            return
        self._set("ready", release, self._ready_message(release), 100)
        if install and self._state.kind == "ready" and self._store.ready is not None:
            self.install_ready()

    # ------------------------------------------------------------------ installing
    def _launch(self, ready: Ready, relaunch: bool) -> str:
        """Pin when rolling back (or past the pin), remember what we're installing, start the installer."""
        before = load_prefs()
        after = self._pin_for(ready.release, before)
        if after != before:
            save_prefs(after)
        self._store.remember_install(ready.release, self._current, before.pinned_version, after.pinned_version,
                                     self._clock())
        error = self._launcher(ready.path, relaunch)
        if error:
            self._store.forget_install()
            if after != before:
                save_prefs(before)
            return error
        self._launched = True
        return ""

    def _pin_for(self, release: Release, prefs: UpdatePrefs) -> UpdatePrefs:
        key = version_key(release.version)
        if key < version_key(self._current):
            return replace(prefs, pinned_version=release.version)  # a rollback stays put
        if prefs.pinned_version and key > version_key(prefs.pinned_version):
            return replace(prefs, pinned_version=release.version)
        return prefs

    def _install_at_start(self) -> None:
        if self._store.ready is not None and self.prefs.mode == "auto" and not self.busy:
            self.install_ready()

    def _on_session_end(self, *_) -> None:
        self._session_end_at = time.monotonic()

    def _session_is_ending(self) -> bool:
        """Windows is signing out or shutting down: an installer started now would be killed."""
        recent = self._session_end_at is not None and time.monotonic() - self._session_end_at < SESSION_END_GRACE_S
        return recent or update_platform.session_ending()

    # ------------------------------------------------------------------ remembering between runs
    def _restore_installed(self) -> bool:
        """Report how the last install went. True if it may still be running (leave its files alone)."""
        record = self._store.load_installed()
        if record is None:
            return False
        release = record.release
        if version_key(release.version) == version_key(self._current):
            self._store.forget_install()
            self._store.clean()
            self._just_updated = release
            back = record.previous is not None and is_newer(record.previous, release.version)
            verb = "went back to version" if back else "was updated to"
            self._set("current", release, f"SnipForge {verb} {release.version}.")
            return False
        if record.started is not None and abs(self._clock() - record.started) < RECENT_INSTALL:
            return True  # started again while the installer may still be running; look again next time
        self._store.forget_install()
        self._store.clean()
        prefs = load_prefs()
        if record.pin_after != record.pin_before and prefs.pinned_version == record.pin_after:
            save_prefs(replace(prefs, pinned_version=record.pin_before))  # the rollback didn't happen
        self._set("error", release, f"The update to SnipForge {release.version} didn't finish, so you're "
                                    f"still on {self._current}. It will be tried again after the next check.")
        return False

    def _restore_ready(self) -> None:
        ready = self._store.restore()
        if ready is None:
            return
        if not self._eligible(ready.release):
            self._store.discard()
            return
        self._set("ready", ready.release, self._ready_message(ready.release), 100)
        if self.prefs.mode == "auto" and not self.install_blocker(ready.release):
            self._startup_install.start(STARTUP_INSTALL_DELAY_MS)

    # ------------------------------------------------------------------ helpers
    def _set(self, kind: str, release: Release | None = None, message: str = "", progress: int = 0) -> None:
        self._state = UpdateState(kind, release, progress, message, self._last_checked)
        self.state_changed.emit(self._state)

    def _next_serial(self) -> int:
        self._serial += 1
        return self._serial

    def _abandon_job(self) -> None:
        job, self._job = self._job, None
        self._next_serial()
        self._install_after_download = False
        if job is not None:
            job.cancel()

    def _eligible(self, release: Release) -> bool:
        """Whether a downloaded update still fits: in the channel, newer, not past the pin, not skipped."""
        prefs = self.prefs
        pick = pick_update([release], self._current, prefs)
        return pick is not None and not is_skipped(release, prefs)

    def _current_message(self, prefs: UpdatePrefs) -> str:
        if prefs.pinned_version:
            return (f"You're on SnipForge {self._current}. Newer versions aren't offered because you chose "
                    f"to stay on {prefs.pinned_version}.")
        return f"You're on the latest version ({self._current})."

    def _ready_message(self, release: Release) -> str:
        if self.prefs.mode == "auto":
            return f"SnipForge {release.version} is ready and will be installed the next time SnipForge starts."
        return f"SnipForge {release.version} is ready to install."

    @staticmethod
    def _skipped_message(release: Release) -> str:
        return f"SnipForge {release.version} is available. You chose to skip this version."
