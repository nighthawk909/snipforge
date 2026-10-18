"""Windows going to sleep, shutting down or signing out, told to the recorder process in time to
finish its file; the screen turning off, and Windows being locked or unlocked, told to it as they
happen; and keeping Windows awake while it records.

Windows tells top-level windows (hidden ones too, but not message-only ones) with
WM_POWERBROADCAST / PBT_APMSUSPEND before sleeping (allowing about 2 s), and with
WM_QUERYENDSESSION then WM_ENDSESSION before ending the session: it waits while WM_ENDSESSION is
being handled, for about 5 s before it offers to end the app anyway, showing the reason given with
ShutdownBlockReasonCreate. So SessionWatch keeps one hidden native window, and its signals are
emitted while the message is being handled: a slot that finishes the recording there and then
(Recorder.finish_now) holds Windows back until the file is complete.

The screen's state comes as WM_POWERBROADCAST / PBT_POWERSETTINGCHANGE for GUID_CONSOLE_DISPLAY_STATE
(RegisterPowerSettingNotification: the current state at once, then each change), locking and
unlocking as WM_WTSSESSION_CHANGE (WTSRegisterSessionNotification).
"""
from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from types import MappingProxyType

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal
from PySide6.QtGui import QWindow

log = logging.getLogger(__name__)

WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_POWERBROADCAST = 0x0218
WM_WTSSESSION_CHANGE = 0x02B1
PBT_APMSUSPEND = 0x0004
PBT_POWERSETTINGCHANGE = 0x8013
WTS_SESSION_LOCK, WTS_SESSION_UNLOCK = 0x7, 0x8
WATCHED = (WM_QUERYENDSESSION, WM_ENDSESSION, WM_POWERBROADCAST, WM_WTSSESSION_CHANGE)
DISPLAY_STATES = MappingProxyType({0: "display off", 1: "display on", 2: "display dimmed"})
ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x00000001, 0x00000002


class GUID(ctypes.Structure):  # fixed sizes (not wintypes'), so the layout is Windows' everywhere
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]


class POWERBROADCAST_SETTING(ctypes.Structure):  # noqa: N801 - the Windows name
    # Data is UCHAR[DataLength]; for the display's state it's one DWORD
    _fields_ = [("PowerSetting", GUID), ("DataLength", ctypes.c_uint32), ("Data", ctypes.c_uint32)]


# GUID_CONSOLE_DISPLAY_STATE {6FE69556-704A-47A0-8F24-C28D936FDA47}: off, on or dimmed (as laid out in memory)
CONSOLE_DISPLAY_STATE = bytes(GUID(0x6FE69556, 0x704A, 0x47A0,
                                   (ctypes.c_ubyte * 8)(0x8F, 0x24, 0xC2, 0x8D, 0x93, 0x6F, 0xDA, 0x47)))


def display_state(lparam: int) -> int | None:
    """The screen's state (0 off, 1 on, 2 dimmed) from a PBT_POWERSETTINGCHANGE's lParam, or None
    if it's about some other power setting, or lParam doesn't point at anything readable (any
    program of the user's could send the message: it mustn't be able to crash a recording)."""
    raw = _read(lparam, ctypes.sizeof(POWERBROADCAST_SETTING)) if lparam > 0 else None
    if raw is None:
        return None
    setting = POWERBROADCAST_SETTING.from_buffer_copy(raw)
    if bytes(setting.PowerSetting) != CONSOLE_DISPLAY_STATE or setting.DataLength < 4:
        return None
    return int(setting.Data)


def _read(address: int, size: int) -> bytes | None:
    """`size` bytes of this process's memory at `address`, or None where they can't be read, which
    reading them directly would crash on. (Elsewhere than Windows no such message ever comes.)"""
    if sys.platform != "win32":
        return ctypes.string_at(address, size)
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                           ctypes.POINTER(ctypes.c_size_t)]
    kernel32.ReadProcessMemory.restype = wintypes.BOOL
    buffer, done = ctypes.create_string_buffer(size), ctypes.c_size_t(0)
    if not kernel32.ReadProcessMemory(kernel32.GetCurrentProcess(), address, buffer, size, ctypes.byref(done)) \
            or done.value != size:
        return None
    return buffer.raw


def classify(message: int, wparam: int, display: int | None = None) -> str | None:
    """What a window message means for a recording: "sleep", "ending?" (Windows asks whether the
    session may end), "ending" (it is ending now), "not ending" (it was called off), "display off",
    "display on", "display dimmed" (`display`: its state, for a PBT_POWERSETTINGCHANGE about the
    screen), "locked", "unlocked", or None."""
    if message == WM_POWERBROADCAST and wparam == PBT_APMSUSPEND:
        return "sleep"
    if message == WM_POWERBROADCAST and wparam == PBT_POWERSETTINGCHANGE and display is not None:
        return DISPLAY_STATES.get(display)
    if message == WM_WTSSESSION_CHANGE:
        return {WTS_SESSION_LOCK: "locked", WTS_SESSION_UNLOCK: "unlocked"}.get(wparam)
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
        # Every window message in the process passes through here (Windows only ever passes MSGs):
        # let the others go quickly.
        msg = wintypes.MSG.from_address(int(message))
        if msg.message not in WATCHED or msg.hWnd != self.watch.window_id:
            return False, 0
        display = None
        if msg.message == WM_POWERBROADCAST and msg.wParam == PBT_POWERSETTINGCHANGE:
            display = display_state(msg.lParam)
        event = classify(msg.message, msg.wParam, display)
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
    display_off = Signal()  # the screen turned off (so it isn't changing: a recording shows the last picture)
    display_on = Signal()
    locked = Signal()  # Windows was locked (Win+L, or by policy)
    unlocked = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.window_id = 0
        self._window = self._filter = self._display_note = None
        self._session_note = False
        if sys.platform == "win32" and QCoreApplication.instance() is not None:
            self._window = QWindow()  # never shown: only there to be told
            self._window.create()
            self.window_id = int(self._window.winId())
            self._filter = _Filter(self)
            QCoreApplication.instance().installNativeEventFilter(self._filter)
            self._display_note, self._session_note = _listen(self.window_id)

    def handle(self, event: str) -> None:
        log.info("Windows says: %s", event)
        signal = {"sleep": self.sleeping, "ending?": self.ending_soon, "ending": self.ending,
                  "not ending": self.not_ending, "display off": self.display_off, "display on": self.display_on,
                  "locked": self.locked, "unlocked": self.unlocked}.get(event)
        if signal is not None:  # "display dimmed": nothing to do
            signal.emit()

    def block_shutdown(self, reason: str) -> None:
        """What Windows shows if it has to wait for us (or offers to end SnipForge anyway)."""
        if self.window_id:
            ctypes.windll.user32.ShutdownBlockReasonCreate(ctypes.c_void_p(self.window_id), ctypes.c_wchar_p(reason))

    def allow_shutdown(self) -> None:
        if self.window_id:
            ctypes.windll.user32.ShutdownBlockReasonDestroy(ctypes.c_void_p(self.window_id))

    def close(self) -> None:
        """Stop watching. Not while Windows is telling the window something (from one of the signals):
        Qt goes on using the window after its filters."""
        app = QCoreApplication.instance()
        if self._filter is not None and app is not None:
            app.removeNativeEventFilter(self._filter)
        self._filter = None
        if self.window_id:
            _stop_listening(self.window_id, self._display_note, self._session_note)
        self._display_note, self._session_note = None, False
        if self._window is not None:
            self.allow_shutdown()
            self._window.destroy()
            self._window = None
            self.window_id = 0


def _listen(window: int) -> tuple:
    """Have Windows tell `window` about the screen turning off and on, and about locking and
    unlocking. Either may be refused (an old or locked-down Windows): the recording goes on without."""
    display, session = None, False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.RegisterPowerSettingNotification.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID), wintypes.DWORD]
        user32.RegisterPowerSettingNotification.restype = wintypes.HANDLE
        guid = GUID.from_buffer_copy(CONSOLE_DISPLAY_STATE)
        display = user32.RegisterPowerSettingNotification(window, ctypes.byref(guid), 0) or None  # to a window
        if display is None:
            log.info("Windows won't say when the screen turns off (error %d)", ctypes.get_last_error())
    except (OSError, AttributeError) as exc:
        log.info("Windows can't say when the screen turns off: %s", exc)
    try:
        wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
        wtsapi32.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
        wtsapi32.WTSRegisterSessionNotification.restype = wintypes.BOOL
        session = bool(wtsapi32.WTSRegisterSessionNotification(window, 0))  # NOTIFY_FOR_THIS_SESSION
        if not session:
            log.info("Windows won't say when it's locked (error %d)", ctypes.get_last_error())
    except (OSError, AttributeError) as exc:
        log.info("Windows can't say when it's locked: %s", exc)
    return display, session


def _stop_listening(window: int, display, session: bool) -> None:
    try:
        if display:
            user32 = ctypes.WinDLL("user32")
            user32.UnregisterPowerSettingNotification.argtypes = [wintypes.HANDLE]
            user32.UnregisterPowerSettingNotification.restype = wintypes.BOOL
            if not user32.UnregisterPowerSettingNotification(display):
                log.info("Windows wouldn't stop saying when the screen turns off")
        if session:
            wtsapi32 = ctypes.WinDLL("wtsapi32")
            wtsapi32.WTSUnRegisterSessionNotification.argtypes = [wintypes.HWND]
            wtsapi32.WTSUnRegisterSessionNotification.restype = wintypes.BOOL
            if not wtsapi32.WTSUnRegisterSessionNotification(window):
                log.info("Windows wouldn't stop saying when it's locked")
    except (OSError, AttributeError) as exc:  # Windows lets go of both when the window goes anyway
        log.info("Stopping Windows' notices: %s", exc)


def stay_awake(on: bool) -> bool:
    """While recording, Windows mustn't sleep or turn the screen off for want of a key press (a
    recording of a dark screen is no use), as video players and calls do; once it's over, it may
    again. It's the calling thread's request, so make it on a thread that lasts the whole recording
    (the main one). Sleep the person asks for (the lid, the Start menu) still happens. Returns
    whether Windows took it (False where there's no Windows)."""
    if sys.platform != "win32":
        return False
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.SetThreadExecutionState.argtypes = [wintypes.DWORD]
    kernel32.SetThreadExecutionState.restype = wintypes.DWORD
    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED if on else 0)
    return kernel32.SetThreadExecutionState(flags) != 0
