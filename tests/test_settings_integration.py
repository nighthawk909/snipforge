"""Settings wired into the running app: tray menu, update dialogs, capture and saving preferences."""
import pytest
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from snipforge import prefs
from snipforge.library import Library
from snipforge.settings import app_settings
from tests.test_settings_window import FakeController, release


@pytest.fixture(autouse=True)
def clean_prefs():
    for key in prefs.KEYS.values():
        app_settings().remove(key)
    yield
    for key in prefs.KEYS.values():
        app_settings().remove(key)


@pytest.fixture
def forge(qapp, tmp_path, monkeypatch, no_dialogs):
    from snipforge import app as app_module
    monkeypatch.setattr(app_module.HotkeyListener, "start", lambda self: True)
    monkeypatch.setattr(app_module.HotkeyListener, "stop", lambda self: None)
    monkeypatch.setattr(app_module.UpdateFlow, "start", lambda self: None)  # no update checks in unit tests
    f = app_module.SnipForgeApp(qapp, Library(tmp_path))
    f.indexer._recognize = lambda image: ""
    yield f
    for window in list(f._windows):
        window.close()
    if f._settings is not None:
        f._settings.close()
    f.indexer.close()


def test_tray_menu_offers_settings_and_check_opens_updates(forge, monkeypatch):
    labels = [a.text() for a in forge._menu.actions() if not a.isSeparator()]
    assert "Settings…" in labels and "Start with Windows" not in labels and "Update automatically" not in labels
    checks = []
    monkeypatch.setattr(forge._updates.controller, "check_now", lambda: checks.append(True))
    forge.check_updates()
    assert forge._settings.isVisible() and forge._settings.current_section() == "updates" and checks
    forge.handle_command("settings")
    assert forge._settings.current_section() == "general"


def test_tray_stays_quiet_while_the_updates_page_is_open(qapp, monkeypatch):
    from snipforge.update_flow import UpdateFlow
    controller = FakeController()
    flow = UpdateFlow(QSystemTrayIcon(), controller=controller)
    offered = []
    monkeypatch.setattr(flow, "offer", offered.append)
    flow.ui_shows_updates = lambda: True
    controller.emit(kind="checking")
    controller.emit(kind="available", release=release("0.6.0"))
    for _ in range(5):
        QApplication.processEvents()
    assert offered == []
    flow.ui_shows_updates = lambda: False
    controller.emit(kind="checking")
    controller.emit(kind="available", release=release("0.6.0"))
    for _ in range(20):
        QApplication.processEvents()
    assert offered and offered[-1].version == "0.6.0"


def test_clipboard_copy_can_be_turned_off(forge, white_image):
    QGuiApplication.clipboard().setText("before")
    prefs.set_copy_on_capture(False)
    forge._on_captured(white_image)
    assert QGuiApplication.clipboard().text() == "before"
    prefs.set_copy_on_capture(True)
    forge._on_captured(white_image)
    assert QGuiApplication.clipboard().image().size() == white_image.size()


def test_magnifier_follows_the_setting(qapp):
    from PySide6.QtGui import QPixmap

    from snipforge.capture import _ScreenOverlay
    screen = QGuiApplication.primaryScreen()
    pix = QPixmap(screen.geometry().size())
    prefs.set_magnifier(False)
    assert _ScreenOverlay(screen, pix, [])._magnifier is False
    prefs.set_magnifier(True)
    assert _ScreenOverlay(screen, pix, [])._magnifier is True


def test_save_uses_the_chosen_folder_and_format(qapp, tmp_path, white_image):
    from snipforge.editor.window import EditorWindow
    prefs.set_export_dir(tmp_path / "Shots")
    prefs.set_export_format("jpg")
    lib = Library(tmp_path / "lib")
    w = EditorWindow(white_image, lib, lib.create(white_image))
    w.save()
    assert w.export_path.parent == tmp_path / "Shots" and w.export_path.suffix == ".jpg"
    assert not QImage(str(w.export_path)).isNull()
    w.close()
