"""Share menu behaviour, with Outlook, Teams, OneDrive and Explorer replaced by fakes."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox, QPushButton

from snipforge import share
from snipforge.editor import share_menu
from snipforge.editor.teams_dialog import OPEN, RECENT_KEY, SHARE, TeamsDialog
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
    monkeypatch.setattr(share, "windows_shell_available", lambda: True)  # Windows, with a fake OneDrive
    monkeypatch.setattr(share, "onedrive_accounts",
                        lambda: [share.OneDriveAccount("Work", window._library.root, True)])
    menu = QMenu()
    controller(window)._populate(menu)
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts[0].startswith("Email with Outlook") and any("OneDrive (Work)" in t for t in texts)
    assert "Share to another app…" in texts  # the Windows share sheet


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


def choose(choice, message=""):
    """A stand-in for the dialog's exec: as if you typed `message` and chose `choice`."""
    def exec_(dialog):
        dialog.message.setText(message)
        dialog.choice = choice
        return QDialog.Accepted
    return exec_


def test_teams_just_open_and_cancel(window, fakes, monkeypatch):
    monkeypatch.setattr(TeamsDialog, "exec", choose(OPEN))
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


def test_send_in_teams_opens_the_share_window_with_the_snip_and_copies_the_message(window, fakes, monkeypatch):
    invoked = []
    monkeypatch.setattr(share, "windows_shell_available", lambda: True)
    monkeypatch.setattr(share, "invoke_shell_verb", lambda path, verb, hwnd: invoked.append((path, verb)) or True)
    monkeypatch.setattr(TeamsDialog, "exec", choose(SHARE, "Look at the totals"))
    controller(window).teams()
    [(path, verb)] = invoked
    assert verb == share.WINDOWS_SHARE_VERB and path.exists() and path.suffix == ".png"
    assert QGuiApplication.clipboard().text() == "Look at the totals"
    assert "paste it into Teams' message box" in window.statusBar().currentMessage()
    assert not fakes["opened"]  # no Teams link: you pick the person in Teams


def test_send_in_teams_without_a_message_leaves_the_clipboard_alone(window, fakes, monkeypatch):
    QGuiApplication.clipboard().setText("something you copied")
    monkeypatch.setattr(share, "invoke_shell_verb", lambda path, verb, hwnd: True)
    monkeypatch.setattr(TeamsDialog, "exec", choose(SHARE))
    controller(window).teams()
    assert QGuiApplication.clipboard().text() == "something you copied"
    assert window.statusBar().currentMessage() == "Pick Teams, then who to send it to."


@pytest.mark.parametrize("failure", ["missing", "com error"])
def test_send_in_teams_falls_back_to_pasting_into_teams(window, fakes, monkeypatch, failure):
    def verb(path, verb, hwnd):
        if failure == "com error":
            raise OSError("COM error")
        return False

    monkeypatch.setattr(share, "invoke_shell_verb", verb)
    monkeypatch.setattr(share, "open_teams", lambda: True)
    monkeypatch.setattr(TeamsDialog, "exec", choose(SHARE, "hi"))
    controller(window).teams()
    assert not QGuiApplication.clipboard().image().isNull()  # the snip, ready to paste
    assert fakes["dialogs"] == ["information"] and not fakes["popen"]


def test_send_in_teams_says_why_if_the_snip_cant_be_saved(window, fakes, monkeypatch):
    def broken(*_a):
        raise share.ShareError("The disk is full")

    monkeypatch.setattr(share, "export", broken)
    monkeypatch.setattr(TeamsDialog, "exec", choose(SHARE, "hi"))
    controller(window).teams()
    assert fakes["dialogs"] == ["warning"]


def test_the_chat_path_still_copies_the_snip_and_sends_the_message_in_the_link(window, fakes, monkeypatch):
    def accept(dialog):
        dialog.message.setText("see this")
        dialog.to.setText("alex@co.com")
        dialog._accept()
        return QDialog.Accepted

    monkeypatch.setattr(TeamsDialog, "exec", accept)
    controller(window).teams()
    assert fakes["opened"][-1].endswith("&message=see this")  # QUrl.toString() shows it decoded
    assert not QGuiApplication.clipboard().image().isNull()
    app_settings().remove(RECENT_KEY)


def test_sharing_leaves_recent_recipients_alone(window, fakes, monkeypatch):
    app_settings().setValue(RECENT_KEY, ["kept@co.com"])
    monkeypatch.setattr(share, "invoke_shell_verb", lambda path, verb, hwnd: True)
    monkeypatch.setattr(TeamsDialog, "exec", choose(SHARE))
    controller(window).teams()
    assert app_settings().value(RECENT_KEY) == ["kept@co.com"]
    app_settings().remove(RECENT_KEY)


def test_the_dialog_leads_with_the_share_window_where_there_is_one(qapp):
    dialog = TeamsDialog(can_share=True)
    assert dialog.share_button.isVisibleTo(dialog) and not dialog.just_open.isVisibleTo(dialog)
    assert dialog.focusWidget() is dialog.message
    assert not any(b.isDefault() for b in dialog.findChildren(QPushButton))
    assert dialog.message.accessibleName() == "Message" and dialog.to.accessibleName() == "Recipient emails"
    dialog.show()
    dialog.message.setFocus()
    QTest.keyClicks(dialog.message, "hello")
    QTest.keyClick(dialog.message, Qt.Key_Return)  # Enter in the message shares
    assert dialog.choice == SHARE and dialog.result() == QDialog.Accepted


def test_enter_presses_the_button_you_tabbed_to(qapp):
    app_settings().remove(RECENT_KEY)
    dialog = TeamsDialog(can_share=True)
    dialog.show()
    dialog.to.setText("alex@co.com")
    dialog.chat_button.setFocus()
    QTest.keyClick(dialog.chat_button, Qt.Key_Return)
    assert dialog.choice == "chat" and dialog.recipients == ["alex@co.com"]
    app_settings().remove(RECENT_KEY)


def test_esc_closes_the_dialog_without_sending(qapp):
    dialog = TeamsDialog(can_share=True)
    dialog.show()
    QTest.keyClick(dialog.message, Qt.Key_Escape)
    assert dialog.result() == QDialog.Rejected and dialog.choice == ""


def test_without_a_share_window_the_dialog_opens_chats(qapp):
    app_settings().setValue(RECENT_KEY, ["alex@co.com"])
    dialog = TeamsDialog(can_share=False)
    assert not dialog.share_button.isVisibleTo(dialog) and dialog.just_open.isVisibleTo(dialog)
    assert dialog.focusWidget() is dialog.to  # who to send to comes first here
    assert not [w for w in QApplication.topLevelWidgets() if isinstance(w, QPushButton)]  # no stray windows
    dialog.show()
    QTest.keyClick(dialog.to, Qt.Key_Return)  # the last recipient is ready: Enter sends to them
    assert dialog.choice == "chat" and dialog.recipients == ["alex@co.com"]
    app_settings().remove(RECENT_KEY)


def test_a_missing_address_puts_you_back_in_the_to_field(qapp):
    app_settings().remove(RECENT_KEY)
    dialog = TeamsDialog(can_share=False)
    dialog.show()
    dialog.message.setFocus()
    QTest.keyClick(dialog.message, Qt.Key_Return)
    assert not dialog.error.isHidden() and dialog.focusWidget() is dialog.to


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

