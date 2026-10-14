"""In-app updates: check daily, ask, then download, verify and install in place.

Per-user installs update themselves: the installer runs silently, keeps your settings and
startup choice, and starts SnipForge again. All-users (IT-managed) installs and source
checkouts just open the download page instead.
"""
from __future__ import annotations

import datetime as dt
import logging
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QSystemTrayIcon

from . import __version__, updates
from .settings import app_settings

log = logging.getLogger(__name__)

FIRST_CHECK_DELAY_MS = 60_000           # don't compete with startup
CHECK_EVERY_MS = 6 * 60 * 60 * 1000     # look every 6 hours; actually checks at most daily
MIN_HOURS_BETWEEN_CHECKS = 20
AUTO_KEY, LAST_CHECK_KEY, SKIP_KEY = "auto_update", "last_update_check", "skip_version"
DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "SnipForge" / "updates"
INSTALLER_ARGS = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER", "/SP-"]


def automatic_enabled() -> bool:
    return app_settings().value(AUTO_KEY, True, bool) and not updates.disabled_by_policy()


def set_automatic(enabled: bool) -> None:
    app_settings().setValue(AUTO_KEY, enabled)


def check_is_due(now: dt.datetime, last_check_iso: str | None) -> bool:
    if not last_check_iso:
        return True
    try:
        last = dt.datetime.fromisoformat(last_check_iso)
    except ValueError:
        return True
    return now - last >= dt.timedelta(hours=MIN_HOURS_BETWEEN_CHECKS)


class UpdateFlow(QObject):
    def __init__(self, tray: QSystemTrayIcon, parent=None) -> None:
        super().__init__(parent)
        self._tray = tray
        self._manual = False
        self._busy = False
        self._checker = updates.UpdateChecker(self)
        self._checker.finished.connect(self._checked)
        self._downloader: updates.UpdateDownloader | None = None
        self._progress: QProgressDialog | None = None
        self._timer = QTimer(self, interval=CHECK_EVERY_MS)
        self._timer.timeout.connect(self._automatic_check)

    # ------------------------------------------------------------------ scheduling
    def start(self) -> None:
        if updates.disabled_by_policy():
            return
        QTimer.singleShot(FIRST_CHECK_DELAY_MS, self._automatic_check)
        self._timer.start()

    def _automatic_check(self) -> None:
        if self._busy or not automatic_enabled():
            return
        if check_is_due(dt.datetime.now(), app_settings().value(LAST_CHECK_KEY, None)):
            self._check(manual=False)

    def check_now(self) -> None:
        if updates.disabled_by_policy():
            QMessageBox.information(None, "SnipForge", "Updates are managed by your organization.")
            return
        if self._busy:
            return
        self._tray.showMessage("SnipForge", "Checking for updates…", QSystemTrayIcon.Information, 2000)
        self._check(manual=True)

    def _check(self, manual: bool) -> None:
        self._manual = manual
        self._busy = True
        self._checker.check()

    # ------------------------------------------------------------------ results
    def _checked(self, status: str, payload) -> None:
        self._busy = False
        if status == "error":
            log.info("Update check failed: %s", payload)
            if self._manual:
                QMessageBox.information(None, "SnipForge", f"Couldn't check for updates: {payload}\n\n"
                                        f"You can check manually at {updates.RELEASES_PAGE}")
            return
        app_settings().setValue(LAST_CHECK_KEY, dt.datetime.now().isoformat(timespec="seconds"))
        release: updates.Release = payload
        if status == "current":
            if self._manual:
                QMessageBox.information(None, "SnipForge", f"You're on the latest version ({__version__}).")
            return
        if not self._manual and app_settings().value(SKIP_KEY, "") == release.version:
            return  # you chose to skip this one; a newer release will ask again
        self.offer(release)

    def offer(self, release: updates.Release) -> None:
        mode = updates.install_mode()
        can_install = mode == "per-user" and release.installer_url is not None
        box = QMessageBox(QMessageBox.Information, "SnipForge update",
                          f"SnipForge {release.version} is available (you have {__version__}).")
        box.setTextFormat(Qt.PlainText)  # release text from the network is never rendered as HTML
        if can_install:
            box.setInformativeText("Update now? SnipForge will close for a moment and reopen by itself.")
        elif mode == "all-users":
            box.setInformativeText("This PC's SnipForge is managed for all users. Ask your IT team to update it, "
                                   "or open the download page.")
        else:
            box.setInformativeText("Open the download page?")
        if release.notes:
            box.setDetailedText(release.notes)
        primary = box.addButton("Update now" if can_install else "Open download page", QMessageBox.AcceptRole)
        skip = box.addButton("Skip this version", QMessageBox.RejectRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.setDefaultButton(primary)
        box.exec()
        clicked = box.clickedButton()
        if clicked is skip:
            app_settings().setValue(SKIP_KEY, release.version)
        elif clicked is primary:
            if can_install:
                self.download(release)
            else:
                QDesktopServices.openUrl(QUrl(release.page))

    # ------------------------------------------------------------------ download and install
    def download(self, release: updates.Release) -> None:
        self._busy = True
        self._downloader = updates.UpdateDownloader(DOWNLOAD_DIR, self)
        self._progress = QProgressDialog(f"Downloading SnipForge {release.version}…", "Cancel", 0, 100)
        self._progress.setWindowTitle("SnipForge update")
        self._progress.setMinimumDuration(0)
        self._progress.canceled.connect(self._downloader.cancel)
        self._downloader.progress.connect(self._on_progress)
        self._downloader.finished.connect(self._downloaded)
        self._progress.show()
        self._downloader.start(release)

    def _on_progress(self, received: int, total: int) -> None:
        if self._progress is not None and total > 0:
            self._progress.setValue(int(received * 100 / total))

    def _downloaded(self, path, error: str) -> None:
        self._busy = False
        if self._progress is not None:
            self._progress.close()
            self._progress = None
        if path is None:
            if "canceled" not in error.lower():
                QMessageBox.warning(None, "SnipForge", f"The update wasn't installed. {error}\n\n"
                                    f"You can download it from {updates.RELEASES_PAGE}")
            return
        self.install(path)

    def install(self, installer: Path) -> None:
        """Run the verified installer silently and quit; the installer starts SnipForge again."""
        try:
            subprocess.Popen([str(installer), *INSTALLER_ARGS], close_fds=True,
                             creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        except OSError as exc:
            QMessageBox.warning(None, "SnipForge", f"Couldn't start the update: {exc}")
            return
        self._tray.showMessage("SnipForge", "Updating… SnipForge will reopen in a moment.",
                               QSystemTrayIcon.Information, 4000)
        QTimer.singleShot(500, QApplication.quit)
