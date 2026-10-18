"""Permission to capture the screen, checked before a snip starts.

Windows lets any app capture the screen. macOS asks for Screen Recording permission (Screen & System
Audio Recording from macOS 15): without it a snip shows little more than the desktop, so SnipForge
checks first and, if it's missing, explains how to turn it on (docs/CROSS-PLATFORM.md). The check
never asks the person anything; only the Open System Settings button does. The macOS calls are in
`_screen_access_mac.py`.
"""
from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

log = logging.getLogger(__name__)

# System Settings > Privacy & Security > Screen (& System Audio) Recording.
SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
TITLE = "Let SnipForge see your screen"
EXPLANATION = (
    "macOS only shows an app the windows of other apps once you allow it to record the screen. Until "
    "then a snip shows little more than your desktop.\n\n"
    "Click Open System Settings and turn on SnipForge under Privacy & Security > Screen & System Audio "
    "Recording (Screen Recording on macOS 14). Then quit SnipForge from its menu bar icon and open it "
    "again: macOS applies the switch when SnipForge restarts.")
OPEN, ANYWAY, CANCEL = "open", "anyway", "cancel"


def asks() -> bool:
    """Whether this platform needs the person's permission to capture the screen."""
    return sys.platform == "darwin"


def granted() -> bool:
    """Whether SnipForge can capture other apps' windows now. True where no permission is needed,
    and when macOS can't be asked (a snip is better than a stuck button)."""
    if not asks():
        return True
    try:
        from . import _screen_access_mac

        return _screen_access_mac.preflight()
    except Exception:  # an old PyObjC, or a macOS without the call: let the snip go ahead
        log.exception("Couldn't check the Screen Recording permission")
        return True


def open_settings() -> None:
    """Adds SnipForge to the Screen Recording list (macOS shows its own prompt the first time) and
    opens that page of System Settings, so the switch is right there."""
    try:
        from . import _screen_access_mac

        _screen_access_mac.request()
    except Exception:
        log.exception("Couldn't ask macOS for the Screen Recording permission")
    if not QDesktopServices.openUrl(QUrl(SETTINGS_URL)):
        log.warning("Couldn't open System Settings at %s", SETTINGS_URL)


def _ask(parent=None) -> str:
    """The explanation, with its three choices: OPEN, ANYWAY or CANCEL."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Information)
    box.setWindowTitle("SnipForge")
    box.setText(TITLE)
    box.setInformativeText(EXPLANATION)
    open_button = box.addButton("Open System Settings", QMessageBox.AcceptRole)
    anyway = box.addButton("Snip anyway", QMessageBox.ActionRole)
    box.addButton(QMessageBox.Cancel)
    box.setDefaultButton(open_button)
    # A snip often starts from the menu bar while another app is in front: bring SnipForge forward
    # with its question rather than leave it behind that app.
    box.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    QTimer.singleShot(0, box, lambda: (box.raise_(), box.activateWindow()) if box.isVisible() else None)
    box.exec()
    clicked = box.clickedButton()
    return OPEN if clicked is open_button else ANYWAY if clicked is anyway else CANCEL


def ready_to_snip(parent=None) -> bool:
    """Call before a snip starts. True to go ahead: the permission is there (or not needed), or the
    person chose Snip anyway. Otherwise it has explained, and opened System Settings if asked."""
    if granted():
        return True
    choice = _ask(parent)
    if choice == OPEN:
        open_settings()
    return choice == ANYWAY
