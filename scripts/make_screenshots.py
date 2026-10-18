"""Render the screenshots in docs/screenshots from the real app windows, with sample content.

    QT_QPA_PLATFORM=windows python scripts/make_screenshots.py

Uses throwaway settings and a temporary library (never yours), draws its own sample "app" to mark
up, and fakes the update engine's state for the Updates page. Run it after UI changes so the README
and the download page show the app as it is.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"
SITE = ROOT / "docs" / "site"
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPointF, QRectF, QSettings, Qt  # noqa: E402

QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tempfile.mkdtemp(prefix="snipforge-shots-"))
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

WIDTH, HEIGHT = 1440, 880


def pump(seconds: float = 0.6) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)


def off_screen(window) -> None:
    """Lay out and paint the window without putting it on screen, so the real mouse can't hover it."""
    window.setAttribute(Qt.WA_DontShowOnScreen, True)
    window.show()


def sample_app(width: int = 1180, height: int = 680, accent: str = "#2F6FEB") -> QImage:
    """A made-up dashboard to mark up (no real product or data)."""
    img = QImage(width, height, QImage.Format_ARGB32)
    img.fill(QColor("#FFFFFF"))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.fillRect(0, 0, width, 52, QColor("#F3F4F6"))
    p.fillRect(0, 52, 210, height - 52, QColor("#F9FAFB"))
    p.setPen(QColor("#111827"))
    p.setFont(QFont("Segoe UI", 13, QFont.DemiBold))
    p.drawText(24, 34, "Contoso Analytics")
    p.setFont(QFont("Segoe UI", 10))
    for n, item in enumerate(("Overview", "Orders", "Customers", "Products", "Reports")):
        if n == 0:
            p.fillRect(12, 72 + n * 40, 186, 32, QColor("#E8EEFF"))
        p.setPen(QColor("#1F2937" if n == 0 else "#6B7280"))
        p.drawText(28, 93 + n * 40, item)
    p.setPen(QColor("#111827"))
    p.setFont(QFont("Segoe UI", 17, QFont.DemiBold))
    p.drawText(240, 98, "Monthly overview")
    tiles = (("Revenue", "$1.24M", "+8%"), ("Orders", "18,402", "+3%"), ("Avg. order", "$67.40", "-2%"))
    for n, (label, value, delta) in enumerate(tiles):
        x = 240 + n * 300
        p.setPen(QPen(QColor("#E5E7EB"), 1))
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(QRectF(x, 120, 280, 96), 10, 10)
        p.setPen(QColor("#6B7280"))
        p.setFont(QFont("Segoe UI", 10))
        p.drawText(x + 18, 148, label)
        p.setPen(QColor("#111827"))
        p.setFont(QFont("Segoe UI", 20, QFont.DemiBold))
        p.drawText(x + 18, 190, value)
        p.setPen(QColor("#15803D" if delta.startswith("+") else "#B91C1C"))
        p.setFont(QFont("Segoe UI", 10, QFont.DemiBold))
        p.drawText(x + 200, 190, delta)
    p.setPen(QPen(QColor("#E5E7EB"), 1))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(240, 236, 880, 300), 10, 10)
    for n, h in enumerate((150, 120, 210, 90, 170, 190, 230, 160)):
        p.fillRect(QRectF(290 + n * 100, 500 - h, 56, h), QColor(accent if n == 3 else "#C9D8FB"))
    p.setPen(QColor("#6B7280"))
    p.setFont(QFont("Segoe UI", 9))
    for n, month in enumerate(("Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct")):
        p.drawText(QRectF(280 + n * 100, 508, 76, 20), Qt.AlignCenter, month)
    p.setFont(QFont("Segoe UI", 10))
    for n, row in enumerate(("Order #10482 · alex@example.com · $214.00", "Order #10481 · sam@example.com · $89.50")):
        p.setPen(QColor("#374151"))
        p.drawText(244, 572 + n * 34, row)
    p.end()
    return img


def editor_shot(theme_manager, library, mode: str, name: str) -> None:
    from snipforge.editor.window import EditorWindow

    theme_manager.set_mode_preference(mode)
    if len(library.entries()) < 6:  # a few earlier snips for the Recent strip
        for accent in ("#16A34A", "#DC2626", "#9333EA", "#EA580C", "#0891B2"):
            library.create(sample_app(accent=accent))
    image = sample_app()
    entry = library.create(image)
    editor = EditorWindow(image, library, entry, on_new_snip=lambda: None, on_open_library=lambda: None)
    editor.resize(WIDTH, HEIGHT)
    off_screen(editor)
    canvas = editor.canvas

    def draw(tool: str, start: tuple[float, float], end: tuple[float, float]):
        before = set(canvas.annotations())
        canvas.set_tool(tool)
        canvas._begin_create(QPointF(*start))
        canvas._update_create(canvas._drag, QPointF(*end), False)
        canvas._finish_create(canvas._drag)
        canvas._drag = None
        return next(a for a in canvas.annotations() if a not in before)

    draw("blur", (236, 552), (640, 616))  # customer emails
    draw("rect", (586, 236), (684, 512))  # around the dip
    draw("arrow", (700, 660), (650, 524))
    canvas.set_tool("step")
    canvas._add_step(QPointF(600, 226))
    canvas._add_step(QPointF(1120, 140))
    callout = draw("callout", (760, 560), (1010, 630))
    callout.text.setPlainText("Check why June dipped")
    canvas.commit_text()
    canvas.set_tool("select")
    canvas._scene.clearSelection()
    callout.setSelected(True)
    pump(0.8)
    canvas.fit_image()
    pump(0.4)
    editor.grab().save(str(OUT / name))
    editor.close()


def rename_when_free(src: Path, dst: Path, seconds: float = 10) -> None:
    """Rename a folder that antivirus may still be scanning (Windows refuses until it's done)."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)


def library_shot(theme_manager, mode: str) -> None:
    from snipforge.home import HomeWindow
    from snipforge.library import DOCUMENT, Library
    from snipforge.winrects import WindowInfo

    theme_manager.set_mode_preference(mode)
    lib = Library(Path(tempfile.mkdtemp(prefix="snipforge-shots-lib-")))
    colors = ("#2F6FEB", "#16A34A", "#DC2626", "#9333EA", "#EA580C", "#0891B2", "#CA8A04", "#4F46E5")
    titles = (("Monthly overview - Contoso Analytics", "Edge", True, ["q3-review"]),
              ("Invoice approvals - Outlook", "Outlook", False, ["finance"]),
              ("Sprint board - Planner", "Edge", False, []),
              ("Pricing.xlsx - Excel", "Excel", True, ["q3-review"]),
              ("Team standup - Teams", "Teams", False, []),
              ("Onboarding checklist.docx - Word", "Word", False, ["onboarding"]),
              ("Error dialog", "Windows", False, ["bugs"]),
              ("Roadmap deck.pptx - PowerPoint", "PowerPoint", False, ["q3-review"]))
    now = dt.datetime.now()
    for n, (title, app, favorite, tags) in enumerate(titles):
        entry = lib.create(sample_app(accent=colors[n]), source=WindowInfo(None, title, app))
        when = now - dt.timedelta(days=(0 if n < 4 else 1 if n < 7 else 3), minutes=n * 7)
        moved = entry.path.with_name(when.strftime("%Y%m%d-%H%M%S-%f")[:19] + f"-{n}")
        rename_when_free(entry.path, moved)
        entry = lib.get(moved.name)
        lib.update_meta(entry, favorite=favorite, tags=tags, indexed=True, text="sample")
        if favorite:
            (entry.path / DOCUMENT).write_text("{}", encoding="utf-8")
    home = HomeWindow(lib, lambda: None, lambda _i: None, on_settings=lambda: None)
    home.resize(WIDTH, HEIGHT)
    off_screen(home)
    pump(1.5)  # thumbnails load in the background
    home.grab().save(str(OUT / f"library-{mode}.png"))
    home.close()


def capture_shot(theme_manager) -> None:
    """The capture overlay over a sample app: the toolbar, a hovered window and the magnifier."""
    from PySide6.QtCore import QEvent, QRect
    from PySide6.QtGui import QGuiApplication, QMouseEvent, QPixmap

    from snipforge.capture import _ScreenOverlay

    theme_manager.set_mode_preference("dark")
    desk = QPixmap(WIDTH, HEIGHT)
    desk.fill(QColor("#1E3550"))
    painter = QPainter(desk)
    painter.drawImage(90, 70, sample_app(1180, 680))
    painter.end()
    overlay = _ScreenOverlay(QGuiApplication.primaryScreen(), desk, [QRect(90, 70, 1180, 680)], mode="area",
                             toolbar=True, scrolling=True)
    overlay.setAttribute(Qt.WA_DontShowOnScreen, True)
    overlay.resize(WIDTH, HEIGHT)
    overlay.show()
    pump(0.3)
    at = QPointF(708, 481)  # the top edge of the highlighted bar, so the magnifier shows both colors
    QApplication.sendEvent(overlay, QMouseEvent(QEvent.MouseMove, at, overlay.mapToGlobal(at), Qt.NoButton,
                                                Qt.NoButton, Qt.NoModifier))
    pump(0.3)
    overlay.grab().save(str(OUT / "capture-dark.png"))
    overlay.close()


def settings_shots(theme_manager) -> None:
    from snipforge import prefs
    from snipforge.settings_window import SettingsWindow
    from snipforge.shortcuts import GlobalShortcuts
    from snipforge.update_controller import UpdateState
    from tests.test_settings_window import FakeController, release
    from tests.test_shortcuts import FakeApi

    controller = FakeController()
    shortcuts = GlobalShortcuts(api=FakeApi())  # nothing is really registered for a screenshot
    shortcuts.set("repeat", "Ctrl+Shift+R")
    prefs.set_shortcut("repeat", "Ctrl+Shift+R")
    window = SettingsWindow(theme_manager=theme_manager, update_controller=controller, shortcuts=shortcuts,
                            library_root=Path.home() / "Pictures" / "SnipForge" / "Library")
    window.resize(WIDTH, 1040)
    window.updates_page._current = "0.7.0"
    controller.releases = [release("0.8.0", notes="Settings window and update controls", days=7),
                           release("0.7.0", notes="New look, Library search and the update engine", days=3),
                           release("0.6.0", notes="One-click download and company update feeds")]
    controller.releases_changed.emit(controller.releases)
    controller.state = UpdateState(kind="ready", release=controller.releases[0], last_checked=dt.datetime.now(),
                                   message="You have 0.7.0. It's downloaded and matches its published checksum.")
    controller.state_changed.emit(controller.state)
    off_screen(window)
    for mode, section in (("light", "updates"), ("dark", "appearance"), ("light", "capture")):
        theme_manager.set_mode_preference(mode)
        window.show_section(section)
        pump(0.6)
        shot = window.grab()
        if section == "appearance":  # a short page: leave out the empty space below it
            shot = shot.copy(0, 0, shot.width(), 400)
        elif section == "capture":
            shot = shot.copy(0, 0, shot.width(), 800)
        shot.save(str(OUT / f"settings-{section}.png"))
    window.close()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    from snipforge import theme
    from snipforge.theme import manager as manager_module

    manager_module.pack_folders = lambda: []  # built-in look only: no organization branding in public docs
    themes = theme.install(app)
    themes.set_accent("snipforge")
    from snipforge.library import Library

    library = Library(Path(tempfile.mkdtemp(prefix="snipforge-shots-ed-")))
    editor_shot(themes, library, "light", "editor-light.png")
    editor_shot(themes, library, "dark", "editor-dark.png")
    library_shot(themes, "light")
    settings_shots(themes)
    capture_shot(themes)
    hero = QImage(str(OUT / "editor-light.png"))
    hero.save(str(SITE / "editor-preview.png"))
    print("Wrote", ", ".join(sorted(p.name for p in OUT.glob("*.png"))), f"and {SITE / 'editor-preview.png'}")
    print(f"Download page image is {hero.width()}x{hero.height()}: update the <img> width/height if it changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

