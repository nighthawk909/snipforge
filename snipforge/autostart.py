"""Start-with-Windows toggle via the per-user Run registry key."""
from __future__ import annotations

import sys
import winreg
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "SnipForge"


def _command() -> str:
    if getattr(sys, "frozen", False):  # installed build: SnipForge.exe
        return f'"{sys.executable}" --background'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")  # no console window
    return f'"{pythonw if pythonw.exists() else exe}" -m snipforge --background'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except FileNotFoundError:
        return False


def set_enabled(enabled: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
