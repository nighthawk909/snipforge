"""Share a finished snip: Outlook email, Teams, OneDrive links, and the Windows share sheet.

OneDrive and the share sheet are driven through the same Explorer right-click
commands you'd use by hand (IContextMenu), so they work with whatever OneDrive
accounts are signed in, with no extra sign-in for SnipForge.

The Explorer and Outlook parts are Windows only. Elsewhere no OneDrive accounts are found and the
shell commands aren't offered; exporting a snip works everywhere (docs/MAC-PLAN.md has the
macOS share sheet).
"""
from __future__ import annotations

import datetime as dt
import logging
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QImage

if sys.platform == "win32":
    import winreg

log = logging.getLogger(__name__)

SHARE_DIR = Path(tempfile.gettempdir()) / "SnipForge" / "share"
ONEDRIVE_SUBFOLDER = "SnipForge"
ONEDRIVE_SHARE_VERB = "{5250E46F-BB09-D602-5891-F476DC89B701}"
ONEDRIVE_COPY_LINK_VERB = "{5250E46F-BB09-D602-5891-F476DC89B702}"
WINDOWS_SHARE_VERB = "Windows.ModernShare"
PR_ATTACH_CONTENT_ID = "http://schemas.microsoft.com/mapi/proptag/0x3712001F"
KEEPALIVE_S = 120  # shell UIs run asynchronously; keep their COM objects alive meanwhile
STALE_AFTER_S = 24 * 3600

_keepalive: list[tuple[float, object]] = []


class ShareError(RuntimeError):
    pass


@dataclass(frozen=True)
class OneDriveAccount:
    label: str
    folder: Path
    business: bool


def snip_filename(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now()).strftime("Snip %Y-%m-%d %H%M%S.png")


def remove_stale(folder: Path, pattern: str, max_age_s: float = STALE_AFTER_S) -> None:
    """Deletes old temp exports; apps that received them have their own copy by now."""
    cutoff = time.time() - max_age_s
    for f in folder.glob(pattern):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass  # in use or already gone


def export(image: QImage, folder: Path, name: str | None = None) -> Path:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if folder == SHARE_DIR:
            remove_stale(folder, "Snip *.png")
        path = folder / (name or snip_filename())
        counter = 2
        while path.exists():
            path = folder / f"{path.stem.rsplit(' (', 1)[0]} ({counter}).png"
            counter += 1
    except OSError as exc:
        raise ShareError(f"Couldn't create {folder}: {exc}") from exc
    if not image.save(str(path), "PNG"):
        raise ShareError(f"Couldn't write {path}")
    return path


# ---------------------------------------------------------------------------
# OneDrive accounts
# ---------------------------------------------------------------------------
def _reg_value(key, name: str) -> str:
    try:
        return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return ""


def onedrive_accounts() -> list[OneDriveAccount]:
    """Signed-in OneDrive accounts with a local sync folder, work accounts first."""
    accounts: list[OneDriveAccount] = []
    if sys.platform != "win32":
        return accounts
    base = r"Software\Microsoft\OneDrive\Accounts"
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base)
    except OSError:
        return accounts
    with root:
        index = 0
        while True:
            try:
                sub = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            if not (sub.startswith("Business") or sub == "Personal"):
                continue
            with winreg.OpenKey(root, sub) as key:
                folder = _reg_value(key, "UserFolder")
                if not folder or not Path(folder).is_dir():
                    continue
                business = sub.startswith("Business")
                label = (_reg_value(key, "DisplayName") or "Work") if business else "Personal"
                accounts.append(OneDriveAccount(label, Path(folder), business))
    accounts.sort(key=lambda a: not a.business)
    return accounts


# ---------------------------------------------------------------------------
# Explorer right-click commands
# ---------------------------------------------------------------------------
@contextmanager
def _context_menu(path: Path):
    """Yields (IContextMenu, HMENU) for a file and always destroys the menu handle."""
    import pythoncom
    import win32gui
    from win32com.shell import shell, shellcon

    pythoncom.CoInitialize()
    desktop = shell.SHGetDesktopFolder()
    folder_pidl, _ = shell.SHILCreateFromPath(str(path.parent), 0)
    folder = desktop.BindToObject(folder_pidl, None, shell.IID_IShellFolder)
    _, item_pidl, _ = folder.ParseDisplayName(0, None, path.name)
    menu_obj = folder.GetUIObjectOf(0, [item_pidl], shell.IID_IContextMenu, 0)[1]
    hmenu = win32gui.CreatePopupMenu()
    try:
        menu_obj.QueryContextMenu(hmenu, 0, 1, 0x7FFF, shellcon.CMF_NORMAL)
        yield menu_obj, hmenu
    finally:
        win32gui.DestroyMenu(hmenu)  # without this every lookup leaks GDI/USER handles


def _find_verb(menu_obj, hmenu, verb: str) -> int | None:
    import win32gui
    from win32com.shell import shellcon

    def walk(handle):
        for i in range(win32gui.GetMenuItemCount(handle)):
            sub = win32gui.GetSubMenu(handle, i)
            if sub:
                found = walk(sub)
                if found is not None:
                    return found
                continue
            item_id = win32gui.GetMenuItemID(handle, i)
            if item_id in (0, -1, 0xFFFFFFFF):
                continue
            try:
                name = menu_obj.GetCommandString(item_id - 1, shellcon.GCS_VERBW)
            except Exception:  # many extensions don't implement verbs; skip them
                continue
            if name and name.lower() == verb.lower():
                return item_id - 1
        return None

    return walk(hmenu)


def has_shell_verb(path: Path, verb: str) -> bool:
    """Slow (loads every shell extension): call it off the GUI thread when polling."""
    try:
        with _context_menu(path) as (menu_obj, hmenu):
            return _find_verb(menu_obj, hmenu, verb) is not None
    except Exception as exc:  # shell extensions raise assorted COM errors
        log.debug("Context menu lookup failed for %s: %s", path, exc)
        return False


class _OwnerWindowSite:
    """COM site that tells shell commands which window they belong to.

    Explorer gives its context menus a site; modern commands such as the Windows share sheet
    ask that site for the owner window and fail with "Invalid window handle" without one.
    """

    def __init__(self, hwnd: int) -> None:
        import pythoncom
        self._com_interfaces_ = [pythoncom.IID_IOleWindow, pythoncom.IID_IServiceProvider]
        self._public_methods_ = ["GetWindow", "ContextSensitiveHelp", "QueryService"]
        self._hwnd = hwnd

    def GetWindow(self) -> int:  # noqa: N802 - COM method name
        return self._hwnd

    def ContextSensitiveHelp(self, _enter) -> None:  # noqa: N802 - COM method name
        pass

    def QueryService(self, _service, iid):  # noqa: N802 - COM method name
        import pythoncom
        import winerror
        from win32com.server.exception import COMException
        from win32com.server.util import wrap
        if iid == pythoncom.IID_IOleWindow:
            return wrap(self, pythoncom.IID_IOleWindow)
        raise COMException(hresult=winerror.E_NOINTERFACE)


def _set_owner_site(menu_obj, hwnd: int) -> None:
    import pythoncom
    from win32com.axcontrol import axcontrol
    from win32com.server.util import wrap
    try:
        menu_obj.QueryInterface(axcontrol.IID_IObjectWithSite).SetSite(
            wrap(_OwnerWindowSite(hwnd), pythoncom.IID_IOleWindow))
    except Exception as exc:  # some menus don't take a site; classic commands still work
        log.debug("Couldn't give the context menu an owner window: %s", exc)


def invoke_shell_verb(path: Path, verb: str, hwnd: int = 0) -> bool:
    """Runs a right-click command (by verb) on a file. Returns False if it isn't offered."""
    if sys.platform != "win32":
        return False
    import win32con

    with _context_menu(path) as (menu_obj, hmenu):
        offset = _find_verb(menu_obj, hmenu, verb)
        if offset is None:
            return False
        if hwnd:
            _set_owner_site(menu_obj, hwnd)
        menu_obj.InvokeCommand((0, hwnd, offset, None, None, win32con.SW_SHOWNORMAL, 0, None))
    now = time.monotonic()
    _keepalive[:] = [(t, o) for t, o in _keepalive if now - t < KEEPALIVE_S] + [(now, menu_obj)]
    return True


# ---------------------------------------------------------------------------
# Outlook and Teams
# ---------------------------------------------------------------------------
def email_with_outlook(image_path: Path, subject: str, width: int) -> bool:
    """Opens a new Outlook draft with the snip. Returns True if it's shown inline in the body,
    False if it's only attached. Raises ShareError if classic Outlook couldn't be used at all."""
    try:
        import pywintypes
        import win32com.client
    except ImportError as exc:
        raise ShareError("Email needs the pywin32 package (pip install pywin32).") from exc
    cid = f"snip{int(time.time())}"
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        mail = outlook.CreateItem(0)
        mail.Subject = subject
        attachment = mail.Attachments.Add(str(image_path), 1, 0)
        mail.Display(False)
    except pywintypes.com_error as exc:
        raise ShareError("Classic Outlook isn't available, so the image was copied instead. "
                         "Paste it into your email (Ctrl+V).") from exc
    try:  # the draft is open now; failing to inline it just leaves it as an attachment
        attachment.PropertyAccessor.SetProperty(PR_ATTACH_CONTENT_ID, cid)
        mail.HTMLBody = f'<p><img src="cid:{cid}" width="{min(width, 900)}"></p>' + mail.HTMLBody
        return True
    except pywintypes.com_error as exc:
        log.warning("Couldn't place the snip inline: %s", exc)
        return False


def open_teams() -> bool:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    return QDesktopServices.openUrl(QUrl("msteams:"))
