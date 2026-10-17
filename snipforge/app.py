"""Tray application: owns the hotkey, capture flow, library and open windows."""
from __future__ import annotations

import ctypes
import logging
import sys

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import __version__, prefs, project, theme
from .capture import CaptureSession
from .editor.window import EditorWindow
from .home import HomeWindow
from .hotkey import SUPPORTED as HOTKEYS_SUPPORTED
from .hotkey import HotkeyListener
from .indexer import TextIndexer
from .library import DocumentUnreadable, Library
from .pin import PinWindow
from .settings_window import SECTIONS, SettingsWindow
from .single_instance import InstanceServer, send_to_running
from .theme.tokens import BRAND
from .update_flow import UpdateFlow

log = logging.getLogger(__name__)
CAPTURE_DELAY_MS = 120  # let the Start menu and key-up settle before freezing the screen
REHOOK_MS = 5 * 60 * 1000  # reinstall the keyboard hook in case Windows dropped it
APP_ID = "SnipForge.SnipForge"


def build_icon(size: int = 256) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 64, size / 64)  # drawn on a 64-unit grid, rendered at full resolution
    p.setBrush(QColor(BRAND))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setPen(QPen(Qt.white, 5, Qt.SolidLine, Qt.SquareCap))
    for x, y, dx, dy in ((16, 16, 1, 1), (48, 16, -1, 1), (16, 48, 1, -1), (48, 48, -1, -1)):
        p.drawLine(x, y, x + 12 * dx, y)
        p.drawLine(x, y, x, y + 12 * dy)
    p.end()
    return QIcon(pix)


class SnipForgeApp:
    def __init__(self, app: QApplication, library: Library | None = None) -> None:
        self._app = app
        self.library = library or Library()
        self.indexer = TextIndexer(self.library)  # reads the words in snips so Library search finds them
        self._session: CaptureSession | None = None
        self._windows: list = []  # editors and pins kept alive until closed
        self._home: HomeWindow | None = None
        self._settings: SettingsWindow | None = None
        self._icon = build_icon()
        app.setWindowIcon(self._icon)
        app.setQuitOnLastWindowClosed(False)

        self._hotkey = HotkeyListener()
        self._hotkey.set_keys(*prefs.hotkeys())
        self._hotkey.triggered.connect(self.capture, Qt.QueuedConnection)
        self._hook_ok = self._hotkey.start()
        self._rehook = QTimer(interval=REHOOK_MS)
        self._rehook.timeout.connect(self._hotkey.refresh)
        self._rehook.start()
        self._build_tray()
        app.aboutToQuit.connect(self._hotkey.stop)
        app.aboutToQuit.connect(self.indexer.close)

    # ------------------------------------------------------------------ tray
    def _build_tray(self) -> None:
        self._tray = QSystemTrayIcon(self._icon)
        self._update_tray_tip()
        self._updates = UpdateFlow(self._tray)
        self._updates.ui_shows_updates = self._settings_shows_updates
        menu = QMenu()
        menu.addAction("New snip", self.capture)
        self._cancel_snip_action = menu.addAction("Cancel snip", self.cancel_snip)
        self._cancel_snip_action.setVisible(False)  # only while a snip is under way
        menu.addAction("Library", self.show_home)
        menu.addAction("Settings…", self.show_settings)
        menu.addSeparator()
        menu.addAction("Check for updates…", self.check_updates)
        menu.addAction("About SnipForge", self.about)
        menu.addAction("Quit", self._app.quit)
        self._menu = menu
        self._tray.setContextMenu(menu)
        if sys.platform != "darwin":  # a Mac menu bar icon opens its menu on click, and New snip is in it
            self._tray.activated.connect(
                lambda reason: self.capture() if reason == QSystemTrayIcon.Trigger else None)
        self._tray.show()
        if not self._hook_ok and HOTKEYS_SUPPORTED:
            self._tray.showMessage("SnipForge", "Couldn't install the keyboard hook, so Win+Shift+S "
                                   "won't work. Use the tray icon to snip.", QSystemTrayIcon.Warning)
        self._updates.start()

    def _update_tray_tip(self) -> None:
        keys = [name for name, on in zip(("Win+Shift+S", "Print Screen"), prefs.hotkeys(), strict=True)
                if on and HOTKEYS_SUPPORTED]
        click = "click this icon, then New snip" if sys.platform == "darwin" else "click this icon to snip"
        how = " or ".join(keys) if keys else click
        self._tray.setToolTip(f"SnipForge {__version__} — {how}")

    def check_updates(self) -> None:
        """Open Settings > Updates and check there (the tray flow explains when IT turned updates off)."""
        if self._updates.controller.policy.disabled:
            self._updates.check_now()
            return
        self.show_settings("updates")
        if not self._updates.controller.busy:
            self._updates.controller.check_now()

    def _settings_shows_updates(self) -> bool:
        """Settings > Updates is in front of the user right now, so it already shows what's happening."""
        s = self._settings
        return (s is not None and s.isVisible() and not s.isMinimized() and s.isActiveWindow()
                and QGuiApplication.applicationState() == Qt.ApplicationActive
                and s.current_section() == "updates")

    def show_settings(self, section: str = "general") -> None:
        if self._settings is None:
            self._settings = SettingsWindow(theme_manager=theme.get_manager(),
                                            update_controller=self._updates.controller,
                                            hotkey=self._hotkey, library_root=self.library.root,
                                            on_shortcuts_changed=self._update_tray_tip)
        self._settings.show_section(section if section in SECTIONS else "general")
        self._settings.showNormal()
        self._bring_to_front(self._settings)

    def about(self) -> None:
        QMessageBox.about(
            None, "About SnipForge",
            f"<b>SnipForge {__version__}</b><br>Open-source screenshot and markup tool for Windows."
            f"<br><br>Built with Python, Qt and PySide6 (LGPL-3.0). License texts are in the install folder."
            f"<br><br>MIT License. Your snips stay on this PC. SnipForge only goes online to check for "
            f"updates (choose how in Settings) or when you use Share."
            f"<br><br><a href='{project.REPO_URL}'>{project.REPO_URL.removeprefix('https://')}</a>")

    def handle_command(self, command: str) -> None:
        if command == "snip":
            self.capture()
        elif command == "home":
            self.show_home()
        elif command == "settings":
            self.show_settings()

    # ------------------------------------------------------------------ windows
    def show_home(self) -> None:
        if self._home is None:
            self._home = HomeWindow(self.library, self.capture, self.open_entry, indexer=self.indexer,
                                    on_settings=self.show_settings)
        if self._home.isVisible():
            self._home.refresh()  # already open: pick up anything new (opening it refreshes by itself)
        self._home.showNormal()
        self._bring_to_front(self._home)

    def capture(self) -> None:
        if self._session is not None:
            self._session.snip_now()  # pressed again during a countdown: don't wait
            return
        self._session = CaptureSession(self._on_captured)
        self._cancel_snip_action.setVisible(True)
        QTimer.singleShot(CAPTURE_DELAY_MS, self._session.start)

    def cancel_snip(self) -> None:
        """From the tray: stop a snip in progress, e.g. during a countdown, which has no keyboard."""
        if self._session is not None:
            self._session.cancel()

    def _on_captured(self, image: QImage | None) -> None:
        source = self._session.source if self._session is not None else None
        note = getattr(self._session, "note", None)
        self._session = None
        self._cancel_snip_action.setVisible(False)
        if note:  # a scrolling capture that ended early says why
            self._tray.showMessage("SnipForge", note, QSystemTrayIcon.Information, 6000)
        if image is None:
            return
        if prefs.copy_on_capture():  # like Win+Shift+S: the raw snip is on the clipboard too
            QGuiApplication.clipboard().setImage(image)
        try:
            entry = self.library.create(image, source=source)
        except OSError as exc:
            log.warning("Couldn't add snip to library: %s", exc)
            entry = None
        if entry is not None:
            self.indexer.index(entry)
        self._open_editor(image, entry, None)
        if self._home is not None and self._home.isVisible() and entry is not None:
            self._home.update_entry(entry.id)

    def open_entry(self, entry_id: str) -> None:
        for window in self._windows:
            if isinstance(window, EditorWindow) and window._entry and window._entry.id == entry_id:
                self._bring_to_front(window)
                return
        entry = self.library.get(entry_id)
        if entry is None:
            QMessageBox.information(None, "SnipForge", "That snip is no longer in the library.")
            return
        try:
            image = self.library.load_image(entry)
        except OSError as exc:
            QMessageBox.warning(None, "SnipForge", f"Couldn't open that snip: {exc}")
            return
        try:
            document, autosave = self.library.load_document(entry), True
        except DocumentUnreadable as exc:
            log.warning("%s", exc)
            QMessageBox.warning(None, "SnipForge", "Couldn't read this snip's saved markup, so it opens "
                                "without it. Autosave is off for this window so the saved file isn't "
                                "overwritten. Use Save or Copy to keep anything you change.")
            document, autosave = None, False
        self._open_editor(image, entry, document, autosave)

    def _open_editor(self, image: QImage, entry, document, autosave: bool = True) -> None:
        editor = EditorWindow(image, self.library, entry, document, on_new_snip=self.capture,
                              on_open_entry=self.open_entry, on_pin=self.pin, autosave=autosave,
                              on_open_library=self.show_home)
        self._track(editor)
        editor.show()
        self._bring_to_front(editor)

    def pin(self, image: QImage) -> None:
        window = PinWindow(image, QPoint(*self._pin_position()))
        self._track(window)
        window.show()

    def _pin_position(self) -> tuple[int, int]:
        avail = QGuiApplication.primaryScreen().availableGeometry()
        offset = 30 * (sum(isinstance(w, PinWindow) for w in self._windows) % 8)
        return avail.right() - 520 - offset, avail.top() + 60 + offset

    def _track(self, window) -> None:
        window.setAttribute(Qt.WA_DeleteOnClose)
        window.destroyed.connect(lambda _=None, w=window: self._forget(w))
        self._windows.append(window)

    def _forget(self, window) -> None:
        if window in self._windows:
            self._windows.remove(window)
        entry = getattr(window, "_entry", None)
        if entry is None:
            return  # a pin: nothing in the library changed
        self.indexer.index(entry, again=True)  # its saved markup cleared the old words: read it again
        if self._home is not None and self._home.isVisible():
            self._home.update_entry(entry.id)

    @staticmethod
    def _bring_to_front(window) -> None:
        window.setWindowState((window.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        window.raise_()
        window.activateWindow()
        try:  # Windows only lets the foreground process steal focus; nudge it
            hwnd = int(window.winId())
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except (OSError, AttributeError):
            pass


def _command_from_args(argv: list[str]) -> str:
    if "--background" in argv:
        return "background"
    if "--snip" in argv:
        return "snip"
    if "--settings" in argv:
        return "settings"
    return "home"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if sys.platform == "win32":  # taskbar grouping and toast notifications use it
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    app = QApplication(sys.argv)
    if "--self-test" in sys.argv:
        from .selftest import run
        index = sys.argv.index("--self-test")
        report = sys.argv[index + 1] if index + 1 < len(sys.argv) else "snipforge-selftest.json"
        return run(report)
    command = _command_from_args(sys.argv)
    if send_to_running(command):
        return 0  # the running instance handles it (e.g. taskbar click opens the library)
    server = InstanceServer()
    if not server.listening and send_to_running(command):
        return 0  # lost a start-up race with another launch; let that one handle it
    theme.install(app)
    forge = SnipForgeApp(app)
    server.command.connect(forge.handle_command)
    if command != "background":
        QTimer.singleShot(0, lambda: forge.handle_command(command))
    return app.exec()
