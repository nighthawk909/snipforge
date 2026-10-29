"""The platform-specific parts of updating: installer names, install mode, policy and launching.

Everything else in the update engine is platform-neutral. Windows is implemented today; the macOS
port fills in the `macos` branches (a .dmg asset, managed preferences instead of the registry).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PLATFORM = "macos" if sys.platform == "darwin" else "windows"
POLICY_KEY = r"Software\Policies\SnipForge"
POLICY_VALUES = ("DisableUpdateCheck", "UpdateSource", "UpdateFeed", "UpdateMode", "UpdateChannel", "AllowRollback")
# Per-user, silent, no reboot.
INSTALLER_ARGS = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER", "/SP-"]
# How the installer starts SnipForge again once it's done (packaging/installer.iss): with its
# window, in the tray (the installer's default: all that older versions of SnipForge ask for), or not.
REOPEN_WINDOW, REOPEN_TRAY, REOPEN_NONE = "window", "tray", "none"
RELAUNCH_ARGS = {REOPEN_WINDOW: ["/RELAUNCH=window"], REOPEN_TRAY: [], REOPEN_NONE: ["/RELAUNCH=0"]}
INSTALLER_LOG = "installer.log"  # Inno Setup's log of the last install, beside SnipForge's own
SM_SHUTTINGDOWN = 0x2000
ASFW_ANY = 0xFFFFFFFF

# How a release's installer is named on GitHub, per platform; group 1 is the version.
_INSTALLER_NAMES = {
    "windows": re.compile(r"^SnipForge-(.+)-setup\.exe$", re.IGNORECASE),
    "macos": re.compile(r"^SnipForge-(.+)\.dmg$", re.IGNORECASE),
}
_INSTALLER_SUFFIXES = {"windows": (".exe",), "macos": (".dmg", ".pkg")}


def installer_version(name: str, platform: str = PLATFORM) -> str | None:
    """The version text in a GitHub installer asset name, or None if it isn't this platform's installer."""
    pattern = _INSTALLER_NAMES.get(platform)
    match = pattern.match(name or "") if pattern else None
    return match.group(1) if match else None


def installer_suffix_ok(name: str, platform: str = PLATFORM) -> bool:
    return (name or "").lower().endswith(_INSTALLER_SUFFIXES.get(platform, ()))


def install_mode(executable: str | None = None, frozen: bool | None = None) -> str:
    """'per-user' (can update itself silently), 'all-users' (IT-managed), 'by-hand' (a Mac, until it
    can install a .dmg itself: docs/PARITY.md) or 'source'."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if not frozen:
        return "source"
    if PLATFORM != "windows":
        return "by-hand"
    exe = Path(executable or sys.executable).resolve()
    local_programs = Path.home() / "AppData" / "Local" / "Programs"
    return "per-user" if local_programs in exe.parents else "all-users"


def launch_installer(installer: Path, reopen: str = REOPEN_TRAY) -> str:
    """Start the verified installer detached; `reopen` is how it starts SnipForge again. Returns ""
    on success, otherwise what went wrong."""
    if PLATFORM != "windows":
        return "Installing updates isn't supported on this platform yet."
    args = [str(installer), *INSTALLER_ARGS, *RELAUNCH_ARGS[reopen], *_log_args()]
    try:
        subprocess.Popen(args, close_fds=True,
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    except OSError as exc:
        return str(exc)
    if reopen == REOPEN_WINDOW:
        _let_the_next_snipforge_come_to_front()
    return ""


def _let_the_next_snipforge_come_to_front() -> None:
    """Windows only lets a program bring its window to the front if the program in front started
    it. The installer starts the new SnipForge after this one has quit, so allow any program to,
    until the next click or key press."""
    import ctypes
    from ctypes import wintypes

    try:
        ctypes.windll.user32.AllowSetForegroundWindow(wintypes.DWORD(ASFW_ANY))
    except (AttributeError, OSError):
        pass


def _log_args() -> list[str]:
    """Have the installer log what it does (replacing the last install's log), so Report a problem
    can show why an update didn't finish."""
    from . import logs

    folder = logs.folder()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return []
    return [f"/LOG={folder / INSTALLER_LOG}"]


def session_ending() -> bool:
    """True while Windows is signing out or shutting down (an installer started now would be killed)."""
    if PLATFORM != "windows":
        return False
    import ctypes

    try:
        return bool(ctypes.windll.user32.GetSystemMetrics(SM_SHUTTINGDOWN))
    except (AttributeError, OSError):
        return False


def read_policy_hives(key: str = POLICY_KEY) -> list[dict[str, object]]:
    """Policy values from HKLM, then HKCU (highest precedence first). Empty dicts where nothing is set."""
    if PLATFORM != "windows":
        return []
    import winreg

    hives = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        values: dict[str, object] = {}
        try:
            with winreg.OpenKey(hive, key) as handle:
                for name in POLICY_VALUES:
                    try:
                        values[name] = winreg.QueryValueEx(handle, name)[0]
                    except OSError:
                        continue
        except OSError:
            pass
        hives.append(values)
    return hives
