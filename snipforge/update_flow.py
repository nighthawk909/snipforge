"""The tray's side of updates: balloons and dialogs on top of UpdateController.

Per-user installs update themselves: the installer runs silently, keeps your settings and startup
choice, and starts SnipForge again. All-users (IT-managed) installs and source checkouts just open
the download page instead. The Settings window drives the same controller (`UpdateFlow.controller`).
"""
from __future__ import annotations

import logging
from dataclasses import replace

from PySide6.QtCore import QObject, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QSystemTrayIcon

from . import update_policy, updates
from .update_controller import UpdateController, UpdateState, check_is_due  # noqa: F401  (check_is_due: older API)
from .update_policy import MANAGED_MESSAGE
from .update_prefs import load_prefs, save_prefs
from .updates import Release

log = logging.getLogger(__name__)


def automatic_enabled() -> bool:
    """Whether SnipForge checks for updates on its own (the tray's "Update automatically")."""
    policy = update_policy.read_policy()
    return not policy.disabled and policy.apply(load_prefs()).mode != "manual"


def set_automatic(enabled: bool) -> None:
    prefs = load_prefs()
    if not enabled:
        save_prefs(replace(prefs, mode="manual"))
    elif prefs.mode == "manual":
        save_prefs(replace(prefs, mode="ask"))


class UpdateFlow(QObject):
    def __init__(self, tray: QSystemTrayIcon, parent=None, controller: UpdateController | None = None) -> None:
        super().__init__(parent)
        self._tray = tray
        self.controller = controller or UpdateController(self)
        self._manual = False        # the user clicked Check for updates
        self._installing = False    # the user chose Update now: show progress and any error
        self._previous = self.controller.state.kind
        self._progress: QProgressDialog | None = None
        self._whats_new: Release | None = None
        self._dialog_open = False
        self.controller.state_changed.connect(self._on_state)
        tray.messageClicked.connect(self._show_whats_new)

    # ------------------------------------------------------------------ tray actions
    def start(self) -> None:
        self.controller.start()
        release = self.controller.just_updated
        if release is not None:
            self._whats_new = release
            self._tray.showMessage("SnipForge", f"SnipForge was updated to {release.version}. "
                                   "Click here to see what's new.", QSystemTrayIcon.Information, 8000)

    def check_now(self) -> None:
        if self.controller.policy.disabled:
            self._notice(QMessageBox.Information, MANAGED_MESSAGE)
            return
        if self.controller.busy:
            return
        self._manual = True
        self._tray.showMessage("SnipForge", "Checking for updates…", QSystemTrayIcon.Information, 2000)
        self.controller.check_now()

    # ------------------------------------------------------------------ reacting to the controller
    def _on_state(self, state: UpdateState) -> None:
        previous, self._previous = self._previous, state.kind
        if state.kind != "downloading":
            self._close_progress()
        handler = getattr(self, f"_on_{state.kind}", None)
        if handler is not None:
            handler(state, previous)

    def _on_idle(self, state: UpdateState, previous: str) -> None:
        if self._manual and state.message:
            self._manual = False
            self._later(lambda: self._notice(QMessageBox.Information, state.message))

    _on_current = _on_idle

    def _on_error(self, state: UpdateState, previous: str) -> None:
        log.info("Update problem: %s", state.message)
        if not (self._manual or self._installing):
            return  # automatic checks fail quietly and try again later
        self._manual = self._installing = False
        if self.controller.policy.source == "feed":
            hint = "If this keeps happening, ask your IT team."
        else:
            hint = f"You can also download SnipForge from {updates.RELEASES_PAGE}"
        self._later(lambda: self._notice(QMessageBox.Warning, f"{state.message}\n\n{hint}"))

    def _on_available(self, state: UpdateState, previous: str) -> None:
        if self._installing:  # the download was canceled
            self._installing = False
            return
        if self._manual or (previous == "checking" and not self.controller.is_skipped(state.release)):
            self._manual = False
            self._later(lambda: self.offer(state.release), state)

    def _on_downloading(self, state: UpdateState, previous: str) -> None:
        if not self._installing:
            return
        if self._progress is None:
            self._progress = QProgressDialog(f"Downloading SnipForge {state.release.version}…", "Cancel", 0, 100)
            self._progress.setWindowTitle("SnipForge update")
            self._progress.setMinimumDuration(0)
            self._progress.canceled.connect(self.controller.cancel)
            self._progress.show()
        self._progress.setValue(state.progress)

    def _on_ready(self, state: UpdateState, previous: str) -> None:
        if self._installing:
            return  # install_version() installs it straight away
        if not (self._manual or previous in ("checking", "downloading")):
            return  # restored at startup: wait for the next check before asking again
        manual, self._manual = self._manual, False
        if self.controller.prefs.mode == "auto" and not manual:
            self._tray.showMessage("SnipForge", f"SnipForge {state.release.version} will be installed the next "
                                   "time SnipForge starts.", QSystemTrayIcon.Information, 4000)
            return
        self._later(lambda: self.ask_to_install(state.release), state)

    def _on_installing(self, state: UpdateState, previous: str) -> None:
        self._installing = False
        self._tray.showMessage("SnipForge", "Updating… SnipForge will reopen in a moment.",
                               QSystemTrayIcon.Information, 4000)

    # ------------------------------------------------------------------ dialogs
    def _later(self, show, state: UpdateState | None = None) -> None:
        """Open a dialog once the controller's signal has reached every listener (the Settings window
        too), one at a time, and only if the controller is still where it was."""
        def run() -> None:
            now = self.controller.state
            if self._dialog_open:
                return
            if state is not None and (now.kind != state.kind or now.release != state.release):
                return
            self._dialog_open = True
            try:
                show()
            finally:
                self._dialog_open = False

        QTimer.singleShot(0, self, run)

    @staticmethod
    def _notice(icon, text: str) -> None:
        box = QMessageBox(icon, "SnipForge", text)
        box.setTextFormat(Qt.PlainText)  # messages can carry text from a server
        box.addButton(QMessageBox.Ok)
        box.exec()

    def _box(self, text: str, informative: str, release: Release) -> QMessageBox:
        box = QMessageBox(QMessageBox.Information, "SnipForge update", text)
        box.setTextFormat(Qt.PlainText)  # release text from the network is never rendered as HTML
        box.setInformativeText(informative)
        if release.notes:
            box.setDetailedText(release.notes)
        return box

    def offer(self, release: Release) -> None:
        blocker = self.controller.install_blocker(release)
        if blocker:
            informative = blocker + (" Open the download page?" if release.page else "")
        else:
            informative = "Update now? SnipForge will close for a moment and reopen by itself."
        have = self.controller.current_version
        box = self._box(f"SnipForge {release.version} is available (you have {have}).", informative, release)
        primary = None
        if not blocker:
            primary = box.addButton("Update now", QMessageBox.AcceptRole)
        elif release.page:
            primary = box.addButton("Open download page", QMessageBox.AcceptRole)
        skip = box.addButton("Skip this version", QMessageBox.RejectRole)
        later = box.addButton("Later" if primary else "OK", QMessageBox.RejectRole)
        box.setDefaultButton(primary or later)
        box.exec()
        clicked = box.clickedButton()
        if clicked is skip:
            self.controller.skip(release.version)
        elif primary is not None and clicked is primary:
            if blocker:
                QDesktopServices.openUrl(QUrl(release.page))
            else:
                self._installing = True
                self.controller.install_version(release.version)

    def ask_to_install(self, release: Release) -> None:
        have = self.controller.current_version
        box = self._box(f"SnipForge {release.version} is ready to install (you have {have}).",
                        "SnipForge will close for a moment and reopen by itself.", release)
        install = box.addButton("Install now", QMessageBox.AcceptRole)
        skip = box.addButton("Skip this version", QMessageBox.RejectRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.setDefaultButton(install)
        box.exec()
        clicked = box.clickedButton()
        if clicked is install:
            self.controller.install_ready()
        elif clicked is skip:
            self.controller.skip(release.version)

    def _show_whats_new(self) -> None:
        release, self._whats_new = self._whats_new, None
        if release is None:
            return
        box = self._box(f"What's new in SnipForge {release.version}", release.notes or "Bug fixes and improvements.",
                        replace(release, notes=""))
        box.addButton("OK", QMessageBox.AcceptRole)
        box.exec()

    def _close_progress(self) -> None:
        if self._progress is not None:
            self._progress.canceled.disconnect()
            self._progress.close()
            self._progress = None
