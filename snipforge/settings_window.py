"""The Settings window: General, Capture, Saving, Appearance, Updates and About.

Every control here changes something real right away; nothing waits for an OK button."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import __version__, autostart, prefs, project, theme
from .icons import icon
from .settings import app_settings
from .theme.tokens import DARK, LIGHT
from .widgets import NavItem, Segmented, SettingRow, ToggleSwitch, stack

log = logging.getLogger(__name__)

SECTIONS = {
    "general": ("General", "settings", "Startup and the clipboard.", "startup sign in clipboard copy library"),
    "capture": ("Capture", "monitor", "Which keys start a snip and what you see while choosing.",
                "shortcut keyboard win shift print screen hotkey magnifier"),
    "saving": ("Saving", "save", "Where Save puts files, and in which format.",
               "save folder format png jpg beautify export"),
    "appearance": ("Appearance", "palette", "Light or dark, and the accent color used across SnipForge.",
                   "theme dark light accent color match windows"),
    "updates": ("Updates", "refresh", "Choose how SnipForge stays up to date.",
                "update version channel preview stable rollback install automatic"),
    "about": ("About", "info", "Version, license and links.", "about version license privacy"),
}
THEME_CHOICES = (("system", "Match Windows"), ("light", "Light"), ("dark", "Dark"))


class ThemeTile(QAbstractButton):
    """A small preview of a theme: half light, half dark for 'Match Windows'."""

    def __init__(self, key: str, label: str) -> None:
        super().__init__()
        self.key = key
        self.setText(label)
        self.setAccessibleName(label)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(170, 128)

    def paintEvent(self, _e) -> None:  # noqa: N802 - Qt API
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(t.accent.fill if self.isChecked() else t.palette.border), 2 if self.isChecked() else 1))
        p.setBrush(QColor(t.palette.surface))
        p.drawRoundedRect(r, 10, 10)
        preview = QRectF(r.left() + 10, r.top() + 10, r.width() - 20, r.height() - 46)
        halves = {"system": (LIGHT, DARK), "light": (LIGHT, LIGHT), "dark": (DARK, DARK)}[self.key]
        for n, pal in enumerate(halves):
            part = QRectF(preview.left() + n * preview.width() / 2, preview.top(), preview.width() / 2,
                          preview.height())
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(pal.bg if n == 0 else pal.surface))
            p.drawRect(part)
            p.setBrush(QColor(pal.border))
            p.drawRoundedRect(QRectF(part.left() + 8, part.top() + 10, part.width() * 0.55, 6), 3, 3)
            p.setBrush(QColor(t.accent.fill if n == 0 else pal.border))
            p.drawRoundedRect(QRectF(part.left() + 8, part.top() + 24, part.width() * 0.7, 14), 4, 4)
        p.setPen(QPen(QColor(t.palette.border), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(preview)
        font = self.font()
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(t.palette.text))
        p.drawText(QRectF(r.left() + 12, r.bottom() - 30, r.width() - 24, 24), Qt.AlignLeft | Qt.AlignVCenter,
                   self.text())
        if self.hasFocus():
            p.setPen(QPen(QColor(t.accent.fill), 2, Qt.DotLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(4, 4, -4, -4), 8, 8)
        p.end()


class SettingsWindow(QMainWindow):
    def __init__(self, *, theme_manager=None, update_controller=None, hotkey=None, library_root: Path | None = None,
                 on_shortcuts_changed=None, parent=None) -> None:
        super().__init__(parent)
        self._themes = theme_manager
        self._hotkey = hotkey
        self._library_root = library_root
        self._on_shortcuts_changed = on_shortcuts_changed
        self.setWindowTitle("SnipForge settings")
        self.resize(1040, 760)
        root = QWidget()
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._build_nav())
        self._scroll = scroll = QScrollArea()
        scroll.setObjectName("SettingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body.setObjectName("SettingsBody")
        col = QVBoxLayout(body)
        col.setContentsMargins(32, 22, 32, 32)
        col.setSpacing(4)
        self._title = QLabel()
        self._title.setProperty("sfRole", "title")
        self._blurb = QLabel()
        self._blurb.setProperty("sfRole", "secondary")
        self._blurb.setContentsMargins(0, 0, 0, 14)
        col.addWidget(self._title)
        col.addWidget(self._blurb)
        # Only the current page is shown. Hidden pages take no room, so a short page like About
        # doesn't scroll (a QStackedWidget is always as tall as its tallest page, here Updates).
        pages = QVBoxLayout()
        pages.setContentsMargins(0, 0, 0, 0)
        self._pages: dict[str, QWidget] = {}
        builders = {"general": self._general, "capture": self._capture, "saving": self._saving,
                    "appearance": self._appearance, "about": self._about,
                    "updates": lambda: self._updates(update_controller)}
        for key in SECTIONS:
            page = builders[key]()
            wrapper = QWidget()
            wcol = QVBoxLayout(wrapper)
            wcol.setContentsMargins(0, 0, 0, 0)
            wcol.addWidget(page)
            wcol.addStretch(1)
            wrapper.hide()
            self._pages[key] = wrapper
            pages.addWidget(wrapper)
        col.addLayout(pages)
        col.addStretch(1)
        body.setMaximumWidth(900)
        scroll.setWidget(body)
        row.addWidget(scroll, 1)
        self.setCentralWidget(root)
        self.show_section("general")
        theme.on_change(self._retheme)

    # ------------------------------------------------------------------ navigation
    def _build_nav(self) -> QWidget:
        nav = QWidget()
        nav.setObjectName("SettingsNav")
        nav.setAttribute(Qt.WA_StyledBackground, True)
        nav.setFixedWidth(236)
        col = QVBoxLayout(nav)
        col.setContentsMargins(12, 18, 12, 12)
        col.setSpacing(2)
        self._find = QLineEdit()
        self._find.setPlaceholderText("Find a setting")
        self._find.setAccessibleName("Find a setting")
        self._find.setClearButtonEnabled(True)
        self._find_icon = self._find.addAction(icon("search"), QLineEdit.LeadingPosition)
        self._find.textChanged.connect(self._filter_nav)
        col.addWidget(self._find)
        col.addSpacing(10)
        self._nav: dict[str, NavItem] = {}
        for key, (label, icon_name, _blurb, _words) in SECTIONS.items():
            item = NavItem(label, icon_name)
            item.setFocusPolicy(Qt.TabFocus)
            item.clicked.connect(lambda _=False, k=key: self.show_section(k))
            self._nav[key] = item
            col.addWidget(item)
        self._no_results = QLabel("No settings match")
        self._no_results.setProperty("sfRole", "muted")
        self._no_results.setContentsMargins(12, 4, 0, 0)
        self._no_results.hide()
        col.addWidget(self._no_results)
        col.addStretch(1)
        return nav

    def show_section(self, key: str) -> None:
        label, _icon, blurb, _words = SECTIONS[key]
        for k, page in self._pages.items():
            page.setVisible(k == key)
        self._scroll.verticalScrollBar().setValue(0)  # every page opens at its top
        self._title.setText(label)
        self._blurb.setText(blurb)
        for k, item in self._nav.items():
            item.setChecked(k == key)
        self._section = key

    def current_section(self) -> str:
        return self._section

    def _filter_nav(self, text: str) -> None:
        words = text.lower().split()
        shown = 0
        for key, item in self._nav.items():
            label, _icon, blurb, extra = SECTIONS[key]
            haystack = f"{label} {blurb} {extra}".lower()
            item.setVisible(all(w in haystack for w in words))
            shown += not item.isHidden()
        self._no_results.setVisible(shown == 0)

    def showEvent(self, e) -> None:  # noqa: N802 - Qt API
        self.refresh()
        super().showEvent(e)

    def refresh(self) -> None:
        """Re-read every control: things can change elsewhere (the editor's Beautify switch, startup set
        in Windows, the theme toggle) while this window is closed."""
        win_shift_s, print_screen = prefs.hotkeys()
        for switch, value in ((self.startup, autostart.is_enabled()), (self.copy_on_capture, prefs.copy_on_capture()),
                              (self.key_win_shift_s, win_shift_s), (self.key_print_screen, print_screen),
                              (self.magnifier, prefs.magnifier()),
                              (self.beautify, app_settings().value("beautify", False, bool))):
            switch.blockSignals(True)
            switch.setChecked(value)
            switch.blockSignals(False)
        self.folder_row.set_description(str(prefs.export_dir()))
        self._reset_folder.setVisible(prefs.export_dir() != prefs.DEFAULT_EXPORT_DIR)
        self.format.set_value(prefs.export_format())
        self._retheme()

    def _retheme(self, _theme=None) -> None:
        self._find_icon.setIcon(icon("search"))
        if self._themes is not None and hasattr(self, "theme_tiles"):  # changed elsewhere, e.g. the editor toggle
            for key, tile in self.theme_tiles.items():
                tile.setChecked(key == self._themes.mode_preference)
            for key, button in self.accent_buttons.items():
                button.setChecked(key == self._themes.accent_id)
        self.update()

    # ------------------------------------------------------------------ pages
    def _general(self) -> QWidget:
        self.startup = ToggleSwitch("Start SnipForge when I sign in")
        self.startup.setChecked(autostart.is_enabled())
        self.startup.toggled.connect(self._set_startup)
        self.copy_on_capture = ToggleSwitch("Copy every snip to the clipboard")
        self.copy_on_capture.setChecked(prefs.copy_on_capture())
        self.copy_on_capture.toggled.connect(prefs.set_copy_on_capture)
        open_library = QPushButton("Open folder")
        open_library.clicked.connect(lambda: self._open_folder(self._library_root))
        open_library.setAccessibleName("Open the library folder")
        library_row = SettingRow("Library", str(self._library_root or ""), open_library)
        library_row.setVisible(self._library_root is not None)
        return stack(
            SettingRow("Start SnipForge when I sign in", "So Win+Shift+S works right away", self.startup),
            SettingRow("Copy every snip to the clipboard", "Paste it anywhere straight after you snip",
                       self.copy_on_capture),
            library_row)

    def _set_startup(self, on: bool) -> None:
        try:
            autostart.set_enabled(on)
        except OSError as exc:
            QMessageBox.warning(self, "SnipForge", f"Couldn't change the startup setting: {exc}")
        if not on and autostart.enabled_for_all_users():
            QMessageBox.information(self, "SnipForge", "SnipForge is set to start for everyone on this PC "
                                    "(set by your administrator), so it will still start when you sign in.")
        self.startup.blockSignals(True)  # show what really happened
        self.startup.setChecked(autostart.is_enabled())
        self.startup.blockSignals(False)

    def _capture(self) -> QWidget:
        win_shift_s, print_screen = prefs.hotkeys()
        self.key_win_shift_s = ToggleSwitch("Win+Shift+S opens SnipForge")
        self.key_win_shift_s.setChecked(win_shift_s)
        self.key_win_shift_s.toggled.connect(lambda on: self._set_key("win_shift_s", on))
        self.key_print_screen = ToggleSwitch("Print Screen opens SnipForge")
        self.key_print_screen.setChecked(print_screen)
        self.key_print_screen.toggled.connect(lambda on: self._set_key("print_screen", on))
        self.magnifier = ToggleSwitch("Show the magnifier")
        self.magnifier.setChecked(prefs.magnifier())
        self.magnifier.toggled.connect(prefs.set_magnifier)
        note = QLabel("A shortcut you turn off goes back to Windows (Win+Shift+S opens Snipping Tool). "
                      "You can always snip from the tray icon or the Library.")
        note.setProperty("sfRole", "muted")
        note.setWordWrap(True)
        note.setContentsMargins(0, 8, 0, 0)
        return stack(
            SettingRow("Win+Shift+S opens SnipForge", "Instead of Snipping Tool, while SnipForge is running",
                       self.key_win_shift_s),
            SettingRow("Print Screen opens SnipForge", "Alt+Print Screen and Win+Print Screen stay with Windows",
                       self.key_print_screen),
            SettingRow("Show the magnifier", "Pixel-exact zoom next to the pointer while you choose an area",
                       self.magnifier),
            note)

    def _set_key(self, which: str, on: bool) -> None:
        prefs.set_hotkey(which, on)
        if self._hotkey is not None:
            self._hotkey.set_keys(*prefs.hotkeys())
        if self._on_shortcuts_changed is not None:
            self._on_shortcuts_changed()

    def _saving(self) -> QWidget:
        buttons = QWidget()
        brow = QHBoxLayout(buttons)
        brow.setContentsMargins(0, 0, 0, 0)
        change = QPushButton("Change…")
        change.clicked.connect(self._choose_folder)
        self._reset_folder = QPushButton("Use Pictures")
        self._reset_folder.setProperty("variant", "ghost")
        self._reset_folder.clicked.connect(lambda: self._set_folder(None))
        brow.addWidget(self._reset_folder)
        brow.addWidget(change)
        self.folder_row = SettingRow("Save folder", str(prefs.export_dir()), buttons)
        self._reset_folder.setVisible(prefs.export_dir() != prefs.DEFAULT_EXPORT_DIR)
        self.format = Segmented([("png", "PNG"), ("jpg", "JPG")], value=prefs.export_format())
        self.format.changed.connect(prefs.set_export_format)
        self.beautify = ToggleSwitch("Beautify when copying, saving or dragging")
        self.beautify.setChecked(app_settings().value("beautify", False, bool))
        self.beautify.toggled.connect(lambda on: app_settings().setValue("beautify", on))
        return stack(
            self.folder_row,
            SettingRow("File format", "PNG keeps every pixel sharp. JPG makes smaller files for photos.", self.format),
            SettingRow("Beautify when copying, saving or dragging",
                       "Backdrop, rounded corners and a shadow. Also in the editor's bottom bar.", self.beautify))

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Save folder", str(prefs.export_dir()))
        if not folder:
            return
        probe = Path(folder) / ".snipforge-write-test"
        try:
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as exc:
            QMessageBox.warning(self, "SnipForge", f"SnipForge can't save files in {folder}: {exc}")
            return
        self._set_folder(Path(folder))

    def _set_folder(self, folder: Path | None) -> None:
        prefs.set_export_dir(folder)
        self.folder_row.set_description(str(prefs.export_dir()))
        self._reset_folder.setVisible(folder is not None)

    def _appearance(self) -> QWidget:
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        if self._themes is None:
            note = QLabel("Themes become available when SnipForge starts normally.")
            note.setProperty("sfRole", "muted")
            col.addWidget(note)
            return box
        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.theme_tiles: dict[str, ThemeTile] = {}
        for key, label in THEME_CHOICES:
            tile = ThemeTile(key, label)
            tile.setChecked(self._themes.mode_preference == key)
            tile.clicked.connect(lambda _=False, k=key: self._set_theme(k))
            self.theme_tiles[key] = tile
            tiles.addWidget(tile)
        tiles.addStretch(1)
        col.addLayout(tiles)
        heading = QLabel("Accent color")
        heading.setProperty("sfRole", "heading")
        heading.setContentsMargins(0, 18, 0, 6)
        col.addWidget(heading)
        grid = QGridLayout()
        grid.setSpacing(8)
        self.accent_buttons: dict[str, QPushButton] = {}
        for n, accent in enumerate(self._themes.accents()):
            button = QPushButton(accent.name)
            button.setObjectName("AccentChoice")
            button.setCheckable(True)
            button.setChecked(accent.id == self._themes.accent_id)
            button.setIcon(self._swatch(accent.light.fill))
            button.clicked.connect(lambda _=False, a=accent.id: self._set_accent(a))
            self.accent_buttons[accent.id] = button
            grid.addWidget(button, n // 3, n % 3)
        col.addLayout(grid)
        return box

    @staticmethod
    def _swatch(color: str):
        from PySide6.QtGui import QIcon, QPixmap
        pix = QPixmap(40, 40)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(4, 4, 32, 32)
        p.end()
        return QIcon(pix)

    def _set_theme(self, key: str) -> None:
        self._themes.set_mode_preference(key)
        for k, tile in self.theme_tiles.items():
            tile.setChecked(k == key)

    def _set_accent(self, accent_id: str) -> None:
        self._themes.set_accent(accent_id)
        for k, button in self.accent_buttons.items():
            button.setChecked(k == accent_id)

    def _updates(self, controller) -> QWidget:
        if controller is None:
            note = QLabel("Updates aren't available in this copy of SnipForge.")
            note.setProperty("sfRole", "muted")
            self.updates_page = None
            return note
        from .updates_page import UpdatesPage
        self.updates_page = UpdatesPage(controller)
        return self.updates_page

    def _about(self) -> QWidget:
        links = QWidget()
        lrow = QHBoxLayout(links)
        lrow.setContentsMargins(0, 0, 0, 0)
        for label, url in (("Source code", project.REPO_URL),
                           ("Release notes", project.RELEASES_URL),
                           ("Report a problem", f"{project.REPO_URL}/issues")):
            button = QPushButton(label)
            button.setProperty("variant", "ghost")
            button.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
            lrow.addWidget(button)
        notices = QPushButton("Third-party notices")
        notices.setProperty("variant", "ghost")
        notices.clicked.connect(self._open_notices)
        lrow.addWidget(notices)
        lrow.addStretch(1)
        privacy = QLabel("Your snips stay on this PC. SnipForge has no accounts, telemetry or analytics. It only goes "
                         "online to check for and download updates, and when you use Share.")
        privacy.setWordWrap(True)
        privacy.setProperty("sfRole", "secondary")
        privacy.setContentsMargins(0, 12, 0, 0)
        return stack(SettingRow(f"SnipForge {__version__}", "Open-source screenshot and markup tool · MIT License"),
                     links, privacy)

    @staticmethod
    def _open_notices() -> None:
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
        path = base / "THIRD_PARTY_NOTICES.md"
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)) if path.exists()
                                 else QUrl(f"{project.REPO_URL}/blob/main/THIRD_PARTY_NOTICES.md"))

    def _open_folder(self, path: Path | None) -> None:
        if path is None:
            return
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))  # noqa: S606 - opens our own library folder in Explorer
        except OSError as exc:
            log.info("Couldn't open %s: %s", path, exc)
            QMessageBox.warning(self, "SnipForge", f"Couldn't open {path}: {exc}")
