"""Share menu behaviour, with Outlook, Teams, OneDrive and Explorer replaced by fakes."""
import pytest
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QDialog, QMenu, QMessageBox

from snipforge import share
from snipforge.editor import share_menu
from snipforge.editor.teams_dialog import RECENT_KEY, TeamsDialog
from snipforge.library import Library
from snipforge.settings import app_settings


@pytest.fixture
def window(qapp, tmp_path, white_image, monkeypatch):
    from snipforge.editor.window import EditorWindow
    monkeypatch.setattr(share, "SHARE_DIR", tmp_path / "share")
    lib = Library(tmp_path / "lib")
    w = EditorWindow(white_image, lib, lib.create(white_image))
    yield w
    w.close()


@pytest.fixture
def fakes(monkeypatch):
    log = {"opened": [], "popen": [], "dialogs": [], "invoked": []}
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: log["opened"].append(url.toString()) or True)
    monkeypatch.setattr(share_menu.subprocess, "Popen", lambda args: log["popen"].append(args))
    for name in ("information", "warning"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, n=name: log["dialogs"].append(n))
    return log


def controller(window):
    return window._share


def test_menu_lists_targets(window, monkeypatch):
    monkeypatch.setattr(share, "onedrive_accounts",
                        lambda: [share.OneDriveAccount("Work", window._library.root, True)])
    menu = QMenu()
    controller(window)._populate(menu)
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts[0].startswith("Email with Outlook") and any("OneDrive (Work)" in t for t in texts)


def test_email_success_and_fallback(window, fakes, monkeypatch):
    calls = []
    monkeypatch.setattr(share, "email_with_outlook", lambda path, subject, width: calls.append(path) or True)
    controller(window).email()
    assert calls and calls[0].exists()

    def no_outlook(*_a):
        raise share.ShareError("Classic Outlook isn't available")

    monkeypatch.setattr(share, "email_with_outlook", no_outlook)
    controller(window).email()
    assert fakes["opened"][-1].startswith("mailto:") and fakes["dialogs"] == ["information"]
    assert not QGuiApplication.clipboard().image().isNull()


def test_teams_opens_chosen_chat(window, fakes, monkeypatch):
    def accept(dialog):
        dialog.to.setText("alex@co.com, sam@co.com")
        dialog._accept()
        return QDialog.Accepted

    monkeypatch.setattr(TeamsDialog, "exec", accept)
    controller(window).teams()
    assert fakes["opened"][-1].startswith("msteams:/l/chat/0/0?users=alex%40co.com,sam%40co.com")


def test_teams_just_open_and_cancel(window, fakes, monkeypatch):
    monkeypatch.setattr(TeamsDialog, "exec", lambda d: 2)
    monkeypatch.setattr(share, "open_teams", lambda: False)
    controller(window).teams()
    assert fakes["dialogs"] == ["information"]
    monkeypatch.setattr(TeamsDialog, "exec", lambda d: QDialog.Rejected)
    controller(window).teams()
    assert fakes["dialogs"] == ["information"]


def test_teams_dialog_validates_and_remembers(qapp):
    app_settings().remove(RECENT_KEY)  # isolated test settings (conftest)
    dialog = TeamsDialog()
    dialog.to.setText("")
    dialog._accept()
    assert not dialog.error.isHidden() and dialog.result() != QDialog.Accepted
    dialog.to.setText("ok@co.com, broken")
    dialog._accept()
    assert "broken" in dialog.error.text()
    dialog.to.setText("ok@co.com")
    dialog._accept()
    assert dialog.recipients == ["ok@co.com"]
    assert TeamsDialog().to.text() == "ok@co.com"  # most recent recipient is prefilled
    app_settings().remove(RECENT_KEY)  # isolated test settings (conftest)


def wait_for(qapp, condition, seconds=10):
    import time
    end = time.time() + seconds
    while not condition() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)
        ctrl = getattr(wait_for, "ctrl", None)
        if ctrl is not None:
            ctrl._poll_onedrive()


def test_onedrive_copy_link_flow(qapp, window, fakes, monkeypatch, tmp_path):
    ctrl = controller(window)
    invoked = []
    monkeypatch.setattr(share, "has_shell_verb", lambda path, verb: True)
    monkeypatch.setattr(share, "invoke_shell_verb", lambda path, verb, hwnd: invoked.append(verb) or True)
    account = share.OneDriveAccount("Personal", tmp_path / "OneDrive", False)
    ctrl.onedrive(account, share.ONEDRIVE_COPY_LINK_VERB)
    ctrl.onedrive(account, share.ONEDRIVE_COPY_LINK_VERB)  # second request while busy is refused
    wait_for.ctrl = ctrl
    wait_for(qapp, lambda: invoked)
    wait_for.ctrl = None
    assert invoked == [share.ONEDRIVE_COPY_LINK_VERB]
    assert list((tmp_path / "OneDrive" / "SnipForge").glob("*.png"))
    QGuiApplication.clipboard().setText("https://1drv.ms/i/example")
    ctrl._clipboard_changed()
    assert "link copied" in window.statusBar().currentMessage()
    ctrl._link_deadline = 1.0
    ctrl._link_timed_out()
    assert "Couldn't confirm" in window.statusBar().currentMessage()


def test_onedrive_gives_up_and_reveals_file(qapp, window, fakes, monkeypatch, tmp_path):
    ctrl = controller(window)
    monkeypatch.setattr(share, "has_shell_verb", lambda path, verb: False)
    monkeypatch.setattr(share_menu, "POLL_LIMIT_S", 0)
    ctrl.onedrive(share.OneDriveAccount("Personal", tmp_path / "OD", False), share.ONEDRIVE_SHARE_VERB)
    wait_for.ctrl = ctrl
    wait_for(qapp, lambda: ctrl._pending is None)
    wait_for.ctrl = None
    assert fakes["popen"] and fakes["dialogs"] == ["information"]


def test_windows_share_and_fallback(window, fakes, monkeypatch):
    invoked = []
    monkeypatch.setattr(share, "invoke_shell_verb", lambda path, verb, hwnd: invoked.append(verb) or True)
    controller(window).windows_share()
    assert invoked == [share.WINDOWS_SHARE_VERB] and not fakes["popen"]

    def broken(*_a):
        raise OSError("COM error")

    monkeypatch.setattr(share, "invoke_shell_verb", broken)
    controller(window).windows_share()
    assert fakes["popen"] and fakes["dialogs"] == ["information"]


@pytest.mark.windows
def test_show_in_folder_saves_first(window, fakes, monkeypatch, tmp_path):
    from snipforge import prefs
    monkeypatch.setattr(prefs, "export_dir", lambda: tmp_path / "exports")
    controller(window).show_in_folder()
    assert window.export_path.exists() and fakes["popen"][0][0] == "explorer"
    assert window.export_path.parent == tmp_path / "exports"


def test_show_in_folder_uses_finder_on_a_mac(window, fakes, monkeypatch, tmp_path, on_a_mac):
    from snipforge import prefs
    monkeypatch.setattr(prefs, "export_dir", lambda: tmp_path / "exports")
    on_a_mac(share_menu)
    controller(window).show_in_folder()
    assert fakes["popen"] == [["open", "-R", str(window.export_path)]]
