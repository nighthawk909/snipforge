"""Start-with-Windows toggle via the per-user Run registry key.

Windows only: elsewhere startup reads as off and turning it on explains why it can't.
"""
from __future__ import annotations

import sys
from pathlib import Path

SUPPORTED = sys.platform == "win32"
if SUPPORTED:
    import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "SnipForge"


def _command() -> str:
    if getattr(sys, "frozen", False):  # installed build: SnipForge.exe
        return f'"{sys.executable}" --background'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")  # no console window
    return f'"{pythonw if pythonw.exists() else exe}" -m snipforge --background'


def _has_value(hive) -> bool:
    if not SUPPORTED:
        return False
    try:
        with winreg.OpenKey(hive, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except FileNotFoundError:
        return False


def enabled_for_all_users() -> bool:
    """IT (or an all-users install) can turn startup on machine-wide; only an admin can undo it."""
    return SUPPORTED and _has_value(winreg.HKEY_LOCAL_MACHINE)


def is_enabled() -> bool:
    return SUPPORTED and (_has_value(winreg.HKEY_CURRENT_USER) or enabled_for_all_users())


def set_enabled(enabled: bool) -> None:
    if not SUPPORTED:
        raise OSError("starting SnipForge when you sign in isn't available on this platform yet")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
