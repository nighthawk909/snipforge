"""Opening SnipForge again while it's running brings up the Library, on both platforms.

On Windows a second launch hands its command to the running SnipForge (single_instance.py). macOS
never starts a second copy from Finder or the Dock: it reactivates the running one, and Qt passes the
Dock's "reopen" on as the application becoming active again (docs/CROSS-PLATFORM.md). So on a Mac
SnipForge watches its activation: it brings up the Library when it's reactivated while already
active (a Dock click), or activated with no window of its own to show (opened again from Finder or
the Dock, the macOS convention). Anything else that activates it (a click on an editor, a snip's
overlay taking the keyboard) leaves it be.
"""
from __future__ import annotations

import sys
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

STARTUP_GRACE_S = 2.0  # SnipForge's own start activates it: what to show then is its command's call
SAME_REOPEN_S = 0.5  # activations this close together are one reopen


def _cocoa() -> bool:
    """Whether Qt is talking to macOS's window server (the Dock and Finder): not under Qt's offscreen
    platform, where tests move the keyboard between windows and the app looks reactivated."""
    return QGuiApplication.platformName() == "cocoa"


def _windows_shown(app: QApplication) -> bool:
    """Whether SnipForge has a window on screen (menus and tooltips don't count)."""
    for widget in app.topLevelWidgets():
        kind = widget.windowType()
        if widget.isVisible() and kind not in (Qt.Popup, Qt.ToolTip):
            return True
    return False


class ReopenWatcher(QObject):
    """Emits `reopened` when, on a Mac, SnipForge is opened again from Finder or the Dock. Does
    nothing elsewhere (Windows starts a second copy, which hands over its command)."""

    reopened = Signal()

    def __init__(self, app: QApplication, parent=None, clock=time.monotonic) -> None:
        super().__init__(parent)
        self._app = app
        self._clock = clock
        self._started = clock()
        self._last = float("-inf")  # when it last emitted
        self._state = app.applicationState()
        self.active = sys.platform == "darwin" and _cocoa()
        if self.active:
            app.applicationStateChanged.connect(self.state_changed)

    def stop(self) -> None:
        if self.active:
            self.active = False
            self._app.applicationStateChanged.disconnect(self.state_changed)

    def state_changed(self, state) -> None:
        was, self._state = self._state, state
        now = self._clock()
        if state != Qt.ApplicationActive or now - self._started < STARTUP_GRACE_S:
            return
        if now - self._last < SAME_REOPEN_S:
            return  # one Dock click can arrive twice: as the activation, then as Qt's reopen
        if was == Qt.ApplicationActive or not _windows_shown(self._app):
            self._last = now
            self.reopened.emit()
