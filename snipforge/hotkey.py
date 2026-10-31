"""Global hotkey capture via a Windows low-level keyboard hook.

Win+Shift+S is reserved by the shell, so RegisterHotKey cannot claim it. A
WH_KEYBOARD_LL hook sees the keystroke first and can swallow it, which stops
the built-in Snipping Tool from ever launching.

Windows only: elsewhere `HotkeyListener.start()` returns False and the app offers its tray icon
instead.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

SUPPORTED = sys.platform == "win32"
# A key that reaches the hook this late means SnipForge was busy: another of its threads held
# Python's lock, so the hook couldn't answer. Windows quietly removes a hook that answers too slowly
# (its limit can be as short as 300 ms), so it's put back at once.
HOOK_LATE_MS = 250
LOG_EVERY_S = 60  # the log says so at most this often (a key stamped with an old time could repeat it)

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_KEYUP = 0x0101
WM_SYSKEYUP = 0x0105
REPEAT_GAP_MS = 1500  # longer than Windows' slowest key-repeat delay (1 s)
WM_QUIT = 0x0012
WM_APP_REHOOK = 0x8001  # private thread message: reinstall the hook
LLKHF_INJECTED = 0x10
KEYEVENTF_KEYUP = 0x0002

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_S = 0x53
VK_SNAPSHOT = 0x2C  # Print Screen
VK_UNASSIGNED = 0xE8  # injected to stop the Start menu opening on Win release

if SUPPORTED:
    from ctypes import wintypes

    ULONG_PTR = ctypes.c_size_t
    LRESULT = ctypes.c_ssize_t

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    _user32.SetWindowsHookExW.restype = wintypes.HHOOK
    _user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    _user32.CallNextHookEx.restype = LRESULT
    _user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, wintypes.DWORD, ULONG_PTR]
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    _kernel32.GetTickCount.restype = wintypes.DWORD  # the clock a key event's `time` is stamped with
else:  # importable everywhere; none of this is used unless the hook is installed
    HOOKPROC = None
    _user32 = _kernel32 = None


def _is_down(vk: int) -> bool:
    return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


class HotkeyListener(QObject):
    """Emits `triggered` when Win+Shift+S or Print Screen is pressed (each can be turned off in
    Settings; a turned-off shortcut is passed through to Windows untouched)."""

    triggered = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._hook = None
        self._proc = HOOKPROC(self._callback) if SUPPORTED else None  # keep a reference or it is GC'd
        self._win_shift_s = True
        self._print_screen = True
        self._held = 0  # the shortcut key being held: Windows repeats it, but it's one snip
        self._held_at = 0  # when it was last seen (ms, the event's own clock)
        self.late_ms = 0  # how late a key reached the hook, until the hook is put back (0: on time)
        self.stalls = 0  # times a late key had the hook put back
        self._logged_at = -LOG_EVERY_S  # when the log last said so (time.monotonic())

    @property
    def keys(self) -> tuple[bool, bool]:
        return self._win_shift_s, self._print_screen

    def set_keys(self, win_shift_s: bool, print_screen: bool) -> None:
        # Plain bools read by the hook thread; assignment is atomic.
        self._win_shift_s, self._print_screen = bool(win_shift_s), bool(print_screen)

    def start(self) -> bool:
        if not SUPPORTED:
            return False
        self._thread = threading.Thread(target=self._run, name="snipforge-hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        return bool(self._hook)

    def stop(self) -> None:
        if self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=1.0)

    def refresh(self) -> None:
        """Reinstall the hook. Windows silently drops a low-level hook that ever responds too
        slowly (e.g. after sleep/resume), so the app calls this periodically."""
        if self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, WM_APP_REHOOK, 0, 0)

    def _install(self):
        return _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, _kernel32.GetModuleHandleW(None), 0)

    def _run(self) -> None:
        self._thread_id = _kernel32.GetCurrentThreadId()
        self._hook = self._install()
        self._ready.set()
        if not self._hook:
            return
        msg = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_APP_REHOOK:
                fresh = self._install()
                if fresh:
                    _user32.UnhookWindowsHookEx(self._hook)
                    self._hook = fresh
                self._reinstalled(bool(fresh))
                continue
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
        _user32.UnhookWindowsHookEx(self._hook)
        self._hook = None

    def _callback(self, n_code, w_param, l_param):
        if n_code >= 0:
            info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            self._check_timing(info.time, _kernel32.GetTickCount())
            if w_param in (WM_KEYUP, WM_SYSKEYUP) and info.vkCode == self._held:
                self._held = 0  # released: the next press is a new snip (the key-up passes on)
            elif w_param in (WM_KEYDOWN, WM_SYSKEYDOWN) and not info.flags & LLKHF_INJECTED and \
                    self._matches(info.vkCode, self._win_shift_s, self._print_screen):
                if not self._repeat(info.vkCode, info.time):
                    if info.vkCode == VK_S:
                        self._cancel_start_menu()
                    self.triggered.emit()
                return 1  # swallow: the shell never sees it (repeats included)
        return _user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _check_timing(self, event_time: int, now: int) -> None:
        """Put the hook back if this key reached it late (both times are Windows' 32-bit tick count,
        which wraps after 49 days). Once per busy spell: later keys in it wait for that reinstall."""
        late = (now - event_time) & 0xFFFFFFFF
        if HOOK_LATE_MS <= late < 0x8000_0000 and not self.late_ms:  # past 2**31: stamped after now
            self.late_ms = late
            self.stalls += 1
            self.refresh()

    def _reinstalled(self, ok: bool = True) -> None:
        """On the hook thread, once it's been put back (or tried): say why in the log if a late key
        asked for it, at most once a minute."""
        if self.late_ms and ok and time.monotonic() - self._logged_at >= LOG_EVERY_S:
            self._logged_at = time.monotonic()
            log.info("SnipForge was busy for %d ms when a key was pressed, so the snip key's keyboard "
                     "hook was put back in case Windows had removed it", self.late_ms)
        self.late_ms = 0

    def _repeat(self, vk: int, when: int) -> bool:
        """Whether this key-down is Windows repeating a held key. A key-down long after the last
        one counts as a new press even if its key-up went missing (e.g. while the hook was reinstalled)."""
        repeat = vk == self._held and 0 <= (when - self._held_at) & 0xFFFFFFFF < REPEAT_GAP_MS
        self._held, self._held_at = vk, when
        return repeat

    @staticmethod
    def _matches(vk: int, win_shift_s: bool = True, print_screen: bool = True) -> bool:
        win = _is_down(VK_LWIN) or _is_down(VK_RWIN)
        if vk == VK_SNAPSHOT:
            # Plain Print Screen only: Alt+PrtSc (active window) and Win+PrtSc stay with Windows.
            return print_screen and not (win or _is_down(VK_MENU) or _is_down(VK_CONTROL))
        return win_shift_s and vk == VK_S and win and _is_down(VK_SHIFT)

    @staticmethod
    def _cancel_start_menu() -> None:
        # Windows opens Start if Win is released with no other key seen in between.
        _user32.keybd_event(VK_UNASSIGNED, 0, 0, 0)
        _user32.keybd_event(VK_UNASSIGNED, 0, KEYEVENTF_KEYUP, 0)
