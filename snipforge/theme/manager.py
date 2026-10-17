"""Owns the active theme: reads the preferences, follows Windows light/dark, applies it app-wide."""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget

from ..settings import app_settings
from . import styles, titlebar
from .packs import ThemePack, discover_packs, pack_folders
from .tokens import BUILTIN_ACCENTS, DEFAULT_ACCENT, Accent, Band, Theme, resolve

log = logging.getLogger(__name__)

MODE_KEY = "appearance/theme"
ACCENT_KEY = "appearance/accent"
PREFERENCES = ("system", "light", "dark")
def _asset_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) / "SnipForge" / "cache" if base else Path(tempfile.gettempdir()) / "SnipForge"
    return root / "theme"


ASSET_DIR = _asset_dir()
ASSET_SVG = {
    "check": '<path d="M3.5 8.5l3 3 6-7"/>',
    "up": '<path d="M4.5 10l3.5-3.5 3.5 3.5"/>',
    "down": '<path d="M4.5 6l3.5 3.5 3.5-3.5"/>',
}
SVG_FRAME = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><g fill="none" stroke="{color}" '
             'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{body}</g></svg>')


class ThemeManager(QObject):
    changed = Signal(object)  # the new Theme

    def __init__(self, app: QApplication, packs: list[ThemePack] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._app = app
        self._accents: dict[str, Accent] = dict(BUILTIN_ACCENTS)
        self._bands: dict[str, Band | None] = {}
        self._default_accent = DEFAULT_ACCENT
        for pack in self._discover() if packs is None else packs:
            if pack.accent.id in self._accents:
                continue
            self._accents[pack.accent.id] = pack.accent
            self._bands[pack.accent.id] = pack.band
            if pack.default and self._default_accent == DEFAULT_ACCENT:
                self._default_accent = pack.accent.id
        self._hints = QGuiApplication.styleHints()
        self._sync_color_scheme()
        self._theme = self._resolve()
        self._hints.colorSchemeChanged.connect(self._on_system_scheme)
        app.installEventFilter(self)

    # ------------------------------------------------------------------ state
    @property
    def theme(self) -> Theme:
        return self._theme

    @property
    def mode_preference(self) -> str:
        value = app_settings().value(MODE_KEY, "system")
        return value if value in PREFERENCES else "system"

    @property
    def accent_id(self) -> str:
        value = app_settings().value(ACCENT_KEY, None)
        return value if value in self._accents else self._default_accent

    def accents(self) -> list[Accent]:
        return list(self._accents.values())

    def set_mode_preference(self, preference: str) -> None:
        if preference not in PREFERENCES:
            raise ValueError(f"unknown theme preference {preference!r}")
        app_settings().setValue(MODE_KEY, preference)
        self._sync_color_scheme()
        self.refresh()

    def set_accent(self, accent_id: str) -> None:
        if accent_id not in self._accents:
            raise ValueError(f"unknown accent {accent_id!r}")
        app_settings().setValue(ACCENT_KEY, accent_id)
        self.refresh()

    # ------------------------------------------------------------------ applying
    def refresh(self) -> None:
        theme = self._resolve()
        if theme == self._theme:
            return
        self._theme = theme
        self.apply()
        self.changed.emit(theme)

    def apply(self) -> None:
        self._app.setPalette(styles.build_palette(self._theme))
        self._app.setStyleSheet(styles.build_stylesheet(self._theme, self._assets()))
        for window in self._app.topLevelWidgets():
            if window.isVisible():
                self.style_window(window)

    def style_window(self, window: QWidget) -> None:
        if window.windowType() not in (Qt.Window, Qt.Dialog) or window.windowFlags() & Qt.FramelessWindowHint:
            return  # menus, tooltips, popups and frameless overlays have no title bar
        header = self._theme.header
        titlebar.style_title_bar(int(window.winId()), self._theme.dark, header.bg, header.text)

    def close(self) -> None:
        """Stop following Windows and new windows (tests create several managers)."""
        self._app.removeEventFilter(self)
        try:
            self._hints.colorSchemeChanged.disconnect(self._on_system_scheme)
        except (RuntimeError, TypeError):
            pass
        self._hints.unsetColorScheme()

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow():
            self.style_window(obj)
        return False

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _discover() -> list[ThemePack]:
        try:
            return discover_packs(pack_folders())
        except Exception:  # theming must never stop SnipForge from starting
            log.exception("Couldn't load theme packs; using the built-in accents")
            return []

    def _effective_mode(self) -> str:
        preference = self.mode_preference
        if preference != "system":
            return preference
        return "dark" if self._hints.colorScheme() == Qt.ColorScheme.Dark else "light"

    def _resolve(self) -> Theme:
        accent_id = self.accent_id
        return resolve(self._effective_mode(), self._accents[accent_id], self._bands.get(accent_id))

    def _sync_color_scheme(self) -> None:
        """Tell Qt (and so native dialogs) which scheme we draw; 'system' hands control back to Windows."""
        preference = self.mode_preference
        if preference == "system":
            self._hints.unsetColorScheme()
        else:
            self._hints.setColorScheme(Qt.ColorScheme.Dark if preference == "dark" else Qt.ColorScheme.Light)

    def _on_system_scheme(self, _scheme) -> None:
        if self.mode_preference == "system":
            self.refresh()

    def _assets(self) -> dict[str, str]:
        """Tiny tinted images the stylesheet needs (check mark, spin and drop-down arrows)."""
        colors = {"check": self._theme.accent.on, "up": self._theme.palette.text2, "down": self._theme.palette.text2}
        paths = {}
        try:
            ASSET_DIR.mkdir(parents=True, exist_ok=True)
            for name, color in colors.items():
                content = SVG_FRAME.format(color=color, body=ASSET_SVG[name])
                path = ASSET_DIR / f"{name}-{color[1:].lower()}.svg"
                if not path.exists() or path.read_text(encoding="utf-8") != content:
                    staging = path.with_suffix(".tmp")
                    staging.write_text(content, encoding="utf-8")
                    os.replace(staging, path)  # never leave a half-written image behind
                paths[name] = str(path)
        except OSError as exc:
            log.info("Couldn't write theme images (%s); using plain controls", exc)
            return {}
        return paths
