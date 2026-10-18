"""Windows going to sleep, shutting down or signing out, told to the recorder process in time to
finish its file.

Windows tells top-level windows (hidden ones too, but not message-only ones) with
WM_POWERBROADCAST / PBT_APMSUSPEND before sleeping (allowing about 2 s), and with
WM_QUERYENDSESSION then WM_ENDSESSION before ending the session: it waits while WM_ENDSESSION is
being handled, for about 5 s before it offers to end the app anyway, showing the reason given with
ShutdownBlockReasonCreate. So SessionWatch keeps one hidden native window, and its signals are
emitted while the message is being handled: a slot that finishes the recording there and then
(Recorder.finish_now) holds Windows back until the file is complete.
"""
from __future__ import annotations

import ctypes
import logging
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal
from PySide6.QtGui import QWindow

log = logging.getLogger(__name__)

WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_POWERBROADCAST = 0x0218
PBT_APMSUSPEND = 0x0004


def classify(message: int, wparam: int) -> str | None:
    """What a window message means for a recording: "sleep", "ending?" (Windows asks whether the
    session may end), "ending" (it is ending now), "not ending" (it was called off), or None."""
    if message == WM_POWERBROADCAST and wparam == PBT_APMSUSPEND:
        return "sleep"
    if message == WM_QUERYENDSESSION:
        return "ending?"
    if message == WM_ENDSESSION:
        return "ending" if wparam else "not ending"
    return None


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, watch: SessionWatch) -> None:
        super().__init__()
        self.watch = watch

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt API
        if bytes(event_type) != b"windows_generic_MSG":
            return False, 0
        from ctypes import wintypes

        msg = wintypes.MSG.from_address(int(message))
        if msg.hWnd != self.watch.window_id:
            return False, 0
        event = classify(msg.message, msg.wParam)
        if event is None:
            return False, 0
        self.watch.handle(event)
        if event == "ending?":
            return True, 1  # yes, it may end: the recording is finished in WM_ENDSESSION
        return False, 0


class SessionWatch(QObject):
    """Signals sleep and the session ending, while Windows waits for them to be handled. Does
    nothing (and never signals) where there's no Windows."""

    sleeping = Signal()
    ending_soon = Signal()  # Windows asked whether the session may end: time to start finishing
    ending = Signal()  # the session is ending: finish now
    not_ending = Signal()  # the shutdown or sign-out was called off

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.window_id = 0
        self._window = self._filter = None
        if sys.platform == "win32" and QCoreApplication.instance() is not None:
            self._window = QWindow()  # never shown: only there to be told
            self._window.create()
            self.window_id = int(self._window.winId())
            self._filter = _Filter(self)
            QCoreApplication.instance().installNativeEventFilter(self._filter)

    def handle(self, event: str) -> None:
        log.info("Windows says: %s", event)
        {"sleep": self.sleeping, "ending?": self.ending_soon, "ending": self.ending,
         "not ending": self.not_ending}[event].emit()

    def block_shutdown(self, reason: str) -> None:
        """What Windows shows if it has to wait for us (or offers to end SnipForge anyway)."""
        if self.window_id:
            ctypes.windll.user32.ShutdownBlockReasonCreate(ctypes.c_void_p(self.window_id), ctypes.c_wchar_p(reason))

    def allow_shutdown(self) -> None:
        if self.window_id:
            ctypes.windll.user32.ShutdownBlockReasonDestroy(ctypes.c_void_p(self.window_id))

    def close(self) -> None:
        if self._filter is not None:
            QCoreApplication.instance().removeNativeEventFilter(self._filter)
            self._filter = None
        if self._window is not None:
            self.allow_shutdown()
            self._window.destroy()
            self._window = None
            self.window_id = 0
