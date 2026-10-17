"""Visible top-level windows, for click-to-capture-a-window in the overlay and for remembering
which app a snip came from (shown and searchable in the Library)."""
from __future__ import annotations

import ctypes
import os
import re
from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtCore import QRect

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
MIN_SIZE = 40
SKIP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TITLE_MAX = 260
APP_NAMES = {
    "excel": "Excel", "winword": "Word", "powerpnt": "PowerPoint", "outlook": "Outlook", "olk": "Outlook",
    "onenote": "OneNote", "msedge": "Edge", "chrome": "Chrome", "firefox": "Firefox", "ms-teams": "Teams",
    "teams": "Teams", "pbidesktop": "Power BI", "explorer": "File Explorer", "code": "VS Code",
    "saplogon": "SAP GUI", "saplgpad": "SAP GUI", "slack": "Slack", "zoom": "Zoom", "notepad": "Notepad",
    "acrord32": "Acrobat", "acrobat": "Acrobat", "applicationframehost": "", "snipforge": "SnipForge",
}


@dataclass(frozen=True)
class WindowInfo:
    rect: QRect | None  # frame in physical pixels
    title: str
    app: str


def friendly_app_name(exe_path: str, description: str = "") -> str:
    """'C:/.../EXCEL.EXE' -> 'Excel'. Unknown programs use their file description ('Battle.net'),
    else a tidied file name."""
    stem = re.split(r"[\\/]", exe_path or "")[-1]
    stem = re.sub(r"\.exe$", "", stem, flags=re.IGNORECASE)
    if not stem:
        return ""
    known = APP_NAMES.get(stem.lower())
    if known is not None:
        return known
    if description.strip():
        return description.strip()[:40]
    return " ".join(part.capitalize() for part in re.split(r"[_\-\s]+", stem) if part)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_dwm = ctypes.WinDLL("dwmapi")
_ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = [_ENUMPROC, wintypes.LPARAM]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD)]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def _cloaked(hwnd) -> bool:
    value = ctypes.c_int(0)
    _dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
    return value.value != 0


def _frame(hwnd) -> QRect | None:
    rect = wintypes.RECT()
    if _dwm.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)):
        return None
    r = QRect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
    return r if r.width() >= MIN_SIZE and r.height() >= MIN_SIZE else None


def _process_path(pid: int) -> str:
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        return buf.value if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else ""
    finally:
        _kernel32.CloseHandle(handle)


_NAMES: dict[str, str] = {}  # program path -> friendly name, kept for the session


def _app_name(path: str) -> str:
    """Known apps by file name; others by their version info, read once per program."""
    if path not in _NAMES:
        known = friendly_app_name(path)
        stem = re.split(r"[\\/]", path or "")[-1].lower().removesuffix(".exe")
        _NAMES[path] = known if stem in APP_NAMES else friendly_app_name(path, _file_description(path))
    return _NAMES[path]


def _file_description(path: str) -> str:
    """The program's own name from its version info, e.g. 'Battle.net'."""
    if not path:
        return ""
    try:
        import win32api

        lang, codepage = win32api.GetFileVersionInfo(path, r"\VarFileInfo\Translation")[0]
        key = rf"\StringFileInfo\{lang:04x}{codepage:04x}\FileDescription"
        return win32api.GetFileVersionInfo(path, key) or ""
    except Exception:  # no version info, access denied, odd resources: just use the file name
        return ""


def window_infos() -> list[WindowInfo]:
    """Visible windows topmost first, with their title and app. Excludes this process's own windows."""
    own_pid = os.getpid()
    found: list[WindowInfo] = []
    name = ctypes.create_unicode_buffer(64)
    title = ctypes.create_unicode_buffer(TITLE_MAX)
    apps: dict[int, str] = {}

    def callback(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd) or _cloaked(hwnd):
            return True
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == own_pid:
            return True
        _user32.GetClassNameW(hwnd, name, len(name))
        if name.value in SKIP_CLASSES:
            return True
        frame = _frame(hwnd)
        if frame is None:
            return True
        if pid.value not in apps:
            apps[pid.value] = _app_name(_process_path(pid.value))
        _user32.GetWindowTextW(hwnd, title, len(title))
        found.append(WindowInfo(frame, title.value.strip(), apps[pid.value]))
        return True

    _user32.EnumWindows(_ENUMPROC(callback), 0)
    return found


def visible_windows() -> list[QRect]:
    """Window frames in physical pixels, topmost first. Excludes this process's own windows."""
    return [w.rect for w in window_infos()]
