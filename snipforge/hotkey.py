"""Global hotkey capture via a Windows low-level keyboard hook.

Win+Shift+S is reserved by the shell, so RegisterHotKey cannot claim it. A
WH_KEYBOARD_LL hook sees the keystroke first and can swallow it, which stops
the built-in Snipping Tool from ever launching.
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_QUIT = 0x0012
WM_APP_REHOOK = 0x8001  # private thread message: reinstall the hook
LLKHF_INJECTED = 0x10
KEYEVENTF_KEYUP = 0x0002

VK_SHIFT = 0x10
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_S = 0x53
VK_SNAPSHOT = 0x2C  # Print Screen
VK_UNASSIGNED = 0xE8  # injected to stop the Start menu opening on Win release

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


def _is_down(vk: int) -> bool:
    return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


class HotkeyListener(QObject):
    """Emits `triggered` when Win+Shift+S or Print Screen is pressed."""

    triggered = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._hook = None
        self._proc = HOOKPROC(self._callback)  # keep a reference or it is GC'd

    def start(self) -> bool:
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
                continue
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
        _user32.UnhookWindowsHookEx(self._hook)
        self._hook = None

    def _callback(self, n_code, w_param, l_param):
        if n_code >= 0 and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
            info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if not info.flags & LLKHF_INJECTED and self._matches(info.vkCode):
                if info.vkCode == VK_S:
                    self._cancel_start_menu()
                self.triggered.emit()
                return 1  # swallow: the shell never sees it
        return _user32.CallNextHookEx(None, n_code, w_param, l_param)

    @staticmethod
    def _matches(vk: int) -> bool:
        if vk == VK_SNAPSHOT:
            return True
        win = _is_down(VK_LWIN) or _is_down(VK_RWIN)
        return vk == VK_S and win and _is_down(VK_SHIFT)

    @staticmethod
    def _cancel_start_menu() -> None:
        # Windows opens Start if Win is released with no other key seen in between.
        _user32.keybd_event(VK_UNASSIGNED, 0, 0, 0)
        _user32.keybd_event(VK_UNASSIGNED, 0, KEYEVENTF_KEYUP, 0)
