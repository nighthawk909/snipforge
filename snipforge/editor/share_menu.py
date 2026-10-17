"""The editor's Share menu: Outlook, Teams, OneDrive link/share, Windows share sheet."""
from __future__ import annotations

import datetime as dt
import logging
import subprocess
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor

from PySide6.QtCore import QObject, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox

from .. import share
from ..icons import icon
from .teams_dialog import TeamsDialog, chat_link

log = logging.getLogger(__name__)

POLL_MS = 1500
POLL_LIMIT_S = 45        # OneDrive usually notices a new file within a few seconds
LINK_WAIT_S = 45         # how long to watch the clipboard for the OneDrive link


def _com_thread_init() -> None:
    if sys.platform != "win32":
        return
    import pythoncom
    pythoncom.CoInitialize()  # shell extensions need an STA on the worker thread


def _show_in_folder(path) -> None:
    """Opens the file's folder with the file selected: Explorer, or Finder on a Mac."""
    subprocess.Popen(["open", "-R", str(path)] if sys.platform == "darwin" else ["explorer", f"/select,{path}"])


class ShareController(QObject):
    def __init__(self, window) -> None:
        super().__init__(window)
        self._window = window
        self._pending: dict | None = None
        self._link_deadline = 0.0
        # Asking Explorer which commands a file has loads every shell extension (~0.3-1.5 s),
        # so polling happens on a worker thread and the editor never freezes.
        self._worker = ThreadPoolExecutor(max_workers=1, initializer=_com_thread_init)
        self._poll = QTimer(self, interval=POLL_MS)
        self._poll.timeout.connect(self._poll_onedrive)
        self._link_timer = QTimer(self, singleShot=True, interval=LINK_WAIT_S * 1000)
        self._link_timer.timeout.connect(self._link_timed_out)
        QGuiApplication.clipboard().dataChanged.connect(self._clipboard_changed)
        window.destroyed.connect(lambda *_: self._worker.shutdown(wait=False, cancel_futures=True))

    # ------------------------------------------------------------------ menu
    def build_menu(self, parent) -> QMenu:
        menu = QMenu(parent)
        menu.aboutToShow.connect(lambda: self._populate(menu))
        return menu

    def _populate(self, menu: QMenu) -> None:
        menu.clear()
        menu.addAction(icon("mail"), "Email with Outlook\tCtrl+E", self.email)
        menu.addAction(icon("chat"), "Send in Teams…", self.teams)
        accounts = share.onedrive_accounts()
        if accounts:
            menu.addSeparator()
        for account in accounts:
            menu.addAction(icon("link"), f"OneDrive ({account.label}): copy link",
                           lambda a=account: self.onedrive(a, share.ONEDRIVE_COPY_LINK_VERB))
            menu.addAction(icon("cloud"), f"OneDrive ({account.label}): share with people…",
                           lambda a=account: self.onedrive(a, share.ONEDRIVE_SHARE_VERB))
        menu.addSeparator()
        menu.addAction(icon("share"), "Teams chat, Outlook and more apps…", self.windows_share)
        menu.addAction(icon("folder"), "Save and show in folder", self.show_in_folder)

    # ------------------------------------------------------------------ helpers
    def _status(self, text: str, ms: int = 6000) -> None:
        self._window.statusBar().showMessage(text, ms)

    def _hwnd(self) -> int:
        return int(self._window.winId())

    def _export_temp(self):
        image = self._window.final_image()
        return image, share.export(image, share.SHARE_DIR)

    def _invoke(self, path, verb: str, unavailable: str) -> bool:
        """Runs a right-click command; on failure shows the file so you can share it by hand."""
        try:
            if share.invoke_shell_verb(path, verb, self._hwnd()):
                return True
            reason = unavailable
        except Exception as exc:  # shell extensions raise assorted COM errors
            log.exception("Shell command %s failed", verb)
            reason = f"{unavailable} ({exc})"
        self._fallback_reveal(path, reason)
        return False

    # ------------------------------------------------------------------ actions
    def email(self) -> None:
        try:
            image, path = self._export_temp()
        except share.ShareError as exc:
            QMessageBox.warning(self._window, "SnipForge", str(exc))
            return
        subject = dt.datetime.now().strftime("Screenshot %b %d, %Y")
        QApplication.setOverrideCursor(Qt.WaitCursor)  # Outlook can take a few seconds to start
        try:
            inline, error = share.email_with_outlook(path, subject, image.width()), None
        except share.ShareError as exc:
            inline, error = False, exc
        finally:
            QApplication.restoreOverrideCursor()
        if error is None:
            self._status("Outlook draft opened with your snip" if inline
                         else "Outlook draft opened with your snip attached")
            return
        QGuiApplication.clipboard().setImage(image)  # fallback: any mail app + paste
        QDesktopServices.openUrl(QUrl(f"mailto:?subject={subject}"))
        QMessageBox.information(self._window, "SnipForge", str(error))

    def teams(self) -> None:
        dialog = TeamsDialog(self._window)
        result = dialog.exec()
        if result == QDialog.Rejected:
            return
        QGuiApplication.clipboard().setImage(self._window.final_image())
        if result == QDialog.Accepted:
            opened = QDesktopServices.openUrl(QUrl(chat_link(dialog.recipients, dialog.message.text().strip())))
            done = "Chat opened in Teams. Click in the message box, press Ctrl+V, then Enter."
        else:
            opened = share.open_teams()
            done = "Image copied. Open a Teams chat and paste it (Ctrl+V)."
        if opened:
            self._status(done, 15000)
        else:
            QMessageBox.information(self._window, "SnipForge", "The image is copied, but Teams didn't open. "
                                    "Open Teams and paste it into a chat (Ctrl+V).")

    def onedrive(self, account: share.OneDriveAccount, verb: str) -> None:
        if self._pending is not None:
            self._status("Still waiting on OneDrive for the last snip…")
            return
        try:
            path = share.export(self._window.final_image(), account.folder / share.ONEDRIVE_SUBFOLDER)
        except share.ShareError as exc:
            QMessageBox.warning(self._window, "SnipForge", str(exc))
            return
        self._pending = {"path": path, "verb": verb, "started": time.monotonic(), "future": None}
        self._status(f"Saved to OneDrive ({account.label}). Waiting for OneDrive…", 0)
        self._poll.start()
        self._poll_onedrive()

    def _poll_onedrive(self) -> None:
        pending = self._pending
        if pending is None:
            self._poll.stop()
            return
        future: Future | None = pending["future"]
        if future is None or (future.done() and not future.result()):
            if time.monotonic() - pending["started"] > POLL_LIMIT_S:
                self._poll.stop()
                self._pending = None
                self._status("")
                self._fallback_reveal(pending["path"], "OneDrive hasn't picked up the file yet.")
                return
            pending["future"] = self._worker.submit(share.has_shell_verb, pending["path"], pending["verb"])
            return
        if not future.done():
            return
        # OneDrive now offers the command: run it here, on the GUI thread, parented to the editor.
        self._poll.stop()
        self._pending = None
        path, verb = pending["path"], pending["verb"]
        if not self._invoke(path, verb, "OneDrive didn't offer that option for the file."):
            self._status("")
            return
        if verb == share.ONEDRIVE_COPY_LINK_VERB:
            self._link_deadline = time.monotonic() + LINK_WAIT_S
            self._link_timer.start()
            self._status("Getting a link from OneDrive…", 0)
        else:
            self._status("OneDrive sharing opened")

    def _clipboard_changed(self) -> None:
        if not self._link_deadline or time.monotonic() > self._link_deadline:
            return
        text = QGuiApplication.clipboard().text().strip()
        if text.startswith("http"):
            self._link_deadline = 0.0
            self._link_timer.stop()
            self._status("OneDrive link copied. Paste it anywhere (Ctrl+V).", 10000)

    def _link_timed_out(self) -> None:
        if self._link_deadline:
            self._link_deadline = 0.0
            self._status("Couldn't confirm the OneDrive link. Check OneDrive's notification.", 10000)

    def windows_share(self) -> None:
        try:
            _image, path = self._export_temp()
        except share.ShareError as exc:
            QMessageBox.warning(self._window, "SnipForge", str(exc))
            return
        self._invoke(path, share.WINDOWS_SHARE_VERB, "The Windows share sheet isn't available here.")

    def show_in_folder(self) -> None:
        self._window.save()
        path = self._window.export_path
        if path is not None and path.exists():
            _show_in_folder(path)

    def _fallback_reveal(self, path, reason: str) -> None:
        QMessageBox.information(self._window, "SnipForge",
                                f"{reason} The snip is saved at:\n{path}\n\nRight-click it in the folder to share.")
        _show_in_folder(path)
