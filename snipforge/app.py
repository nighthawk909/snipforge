"""Tray application: owns the hotkey, capture flow, library and open windows."""
from __future__ import annotations

import ctypes
import logging
import sys

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import REPO, __version__, autostart, theme
from .capture import CaptureSession
from .editor.window import EditorWindow
from .home import HomeWindow
from .hotkey import HotkeyListener
from .library import DocumentUnreadable, Library
from .pin import PinWindow
from .single_instance import InstanceServer, send_to_running
from .theme.tokens import BRAND
from .update_flow import UpdateFlow, automatic_enabled, set_automatic
from .updates import disabled_by_policy

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
        self._session: CaptureSession | None = None
        self._windows: list = []  # editors and pins kept alive until closed
        self._home: HomeWindow | None = None
        self._icon = build_icon()
        app.setWindowIcon(self._icon)
        app.setQuitOnLastWindowClosed(False)

        self._hotkey = HotkeyListener()
        self._hotkey.triggered.connect(self.capture, Qt.QueuedConnection)
        self._hook_ok = self._hotkey.start()
        self._rehook = QTimer(interval=REHOOK_MS)
        self._rehook.timeout.connect(self._hotkey.refresh)
        self._rehook.start()
        self._build_tray()
        app.aboutToQuit.connect(self._hotkey.stop)

    # ------------------------------------------------------------------ tray
    def _build_tray(self) -> None:
        self._tray = QSystemTrayIcon(self._icon)
        self._tray.setToolTip(f"SnipForge {__version__} — Win+Shift+S or Print Screen")
        self._updates = UpdateFlow(self._tray)
        menu = QMenu()
        menu.addAction("New snip", self.capture)
        menu.addAction("Library", self.show_home)
        menu.addSeparator()
        start = QAction("Start with Windows", menu)
        start.setCheckable(True)
        start.setChecked(autostart.is_enabled())
        start.toggled.connect(self._toggle_autostart)
        menu.addAction(start)
        menu.addSeparator()
        menu.addAction("Check for updates…", self.check_updates)
        if not disabled_by_policy():
            auto = QAction("Update automatically", menu)
            auto.setCheckable(True)
            auto.setChecked(automatic_enabled())
            auto.toggled.connect(set_automatic)
            menu.addAction(auto)
        menu.addAction("About SnipForge", self.about)
        menu.addAction("Quit", self._app.quit)
        self._menu = menu
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(
            lambda reason: self.capture() if reason == QSystemTrayIcon.Trigger else None)
        self._tray.show()
        if not self._hook_ok:
            self._tray.showMessage("SnipForge", "Couldn't install the keyboard hook, so Win+Shift+S "
                                   "won't work. Use the tray icon to snip.", QSystemTrayIcon.Warning)
        self._updates.start()

    def _toggle_autostart(self, on: bool) -> None:
        try:
            autostart.set_enabled(on)
        except OSError as exc:
            QMessageBox.warning(None, "SnipForge", f"Couldn't change the startup setting: {exc}")
        if not on and autostart.enabled_for_all_users():
            QMessageBox.information(None, "SnipForge", "SnipForge is set to start for everyone on this PC "
                                    "(set by your administrator), so it will still start when you sign in.")

    def check_updates(self) -> None:
        self._updates.check_now()

    def about(self) -> None:
        QMessageBox.about(
            None, "About SnipForge",
            f"<b>SnipForge {__version__}</b><br>Open-source screenshot and markup tool for Windows."
            f"<br><br>Built with Python, Qt and PySide6 (LGPL-3.0). License texts are in the install folder."
            f"<br><br>MIT License. Your snips stay on this PC. SnipForge only goes online to check GitHub "
            f"for updates (turn off in the tray menu) or when you use Share."
            f"<br><br><a href='https://github.com/{REPO}'>github.com/{REPO}</a>")

    def handle_command(self, command: str) -> None:
        if command == "snip":
            self.capture()
        elif command == "home":
            self.show_home()

    # ------------------------------------------------------------------ windows
    def show_home(self) -> None:
        if self._home is None:
            self._home = HomeWindow(self.library, self.capture, self.open_entry)
        self._home.refresh()
        self._home.showNormal()
        self._bring_to_front(self._home)

    def capture(self) -> None:
        if self._session is not None:
            return
        self._session = CaptureSession(self._on_captured)
        QTimer.singleShot(CAPTURE_DELAY_MS, self._session.start)

    def _on_captured(self, image: QImage | None) -> None:
        source = self._session.source if self._session is not None else None
        self._session = None
        if image is None:
            return
        QGuiApplication.clipboard().setImage(image)  # like Win+Shift+S: the raw snip is on the clipboard too
        try:
            entry = self.library.create(image, source=source)
        except OSError as exc:
            log.warning("Couldn't add snip to library: %s", exc)
            entry = None
        self._open_editor(image, entry, None)
        if self._home is not None and self._home.isVisible():
            self._home.refresh()

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
        if self._home is not None and self._home.isVisible():
            self._home.refresh()

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
    return "home"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
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
