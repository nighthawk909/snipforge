"""Telling the recorder process about the computer going to sleep, shutting down or signing out,
in time to finish its file; about the screen turning off, and being locked or unlocked, as they
happen; and keeping the computer awake while it records.

The operating system's side is in _power_win32.py (Windows: a hidden window it tells, and
SetThreadExecutionState). On the Mac nothing is watched yet: recording is Windows-only for now
(docs/PARITY.md), and the Mac's recording phase adds _power_mac.py (an IOPMAssertion to stay awake,
NSWorkspace's sleep, session and screen notifications), behind this same face.
"""
from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QCoreApplication, QObject, Signal

log = logging.getLogger(__name__)


class SessionWatch(QObject):
    """Signals sleep and the session ending while the system waits for them to be handled (so a
    slot can finish the recording there and then), and the screen and the lock as they change.
    Does nothing (and never signals) where it can't watch yet."""

    sleeping = Signal()
    ending_soon = Signal()  # the system asked whether the session may end: time to start finishing
    ending = Signal()  # the session is ending: finish now
    not_ending = Signal()  # the shutdown or sign-out was called off
    display_off = Signal()  # the screen turned off (so it isn't changing: a recording shows the last picture)
    display_on = Signal()
    locked = Signal()  # the computer was locked (Win+L, or by policy)
    unlocked = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.window_id = 0  # Windows: the hidden window it's told through (told to SnipForge, for tests)
        self._os = None
        if sys.platform == "win32" and QCoreApplication.instance() is not None:
            from . import _power_win32

            self._os = _power_win32.Listener(self)
            self.window_id = self._os.window_id

    def handle(self, event: str) -> None:
        log.info("The system says: %s", event)
        signal = {"sleep": self.sleeping, "ending?": self.ending_soon, "ending": self.ending,
                  "not ending": self.not_ending, "display off": self.display_off, "display on": self.display_on,
                  "locked": self.locked, "unlocked": self.unlocked}.get(event)
        if signal is not None:  # "display dimmed": nothing to do
            signal.emit()

    def block_shutdown(self, reason: str) -> None:
        """What the system shows if it has to wait for us (or offers to end SnipForge anyway)."""
        if self._os is not None:
            self._os.block_shutdown(reason)

    def allow_shutdown(self) -> None:
        if self._os is not None:
            self._os.allow_shutdown()

    def close(self) -> None:
        """Stop watching. Not while the system is telling it something (from one of the signals): Qt
        goes on using Windows' window after its filters."""
        if self._os is not None:
            self._os.close()
            self._os = None
            self.window_id = 0


def stay_awake(on: bool) -> bool:
    """While recording, the computer mustn't sleep or turn the screen off for want of a key press (a
    recording of a dark screen is no use), as video players and calls do; once it's over, it may
    again. It's the calling thread's request, so make it on a thread that lasts the whole recording
    (the main one). Sleep the person asks for (the lid, the Start menu) still happens. Returns
    whether the system took it (False where it can't be asked yet)."""
    if sys.platform != "win32":
        return False
    from ._power_win32 import stay_awake as windows

    return windows(on)
