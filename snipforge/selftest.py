"""`SnipForge.exe --self-test <report.json>`: proves a packaged build actually works.

Exercises the pieces that break when packaging goes wrong (Qt plugins, theme, SVG icons, the editor,
Windows OCR, the keyboard hook, Explorer share commands) and writes a JSON report. Exit code 0
means every required check passed. CI runs this against the built exe before publishing.

On a Mac (docs/MAC-PLAN.md) the Windows-only checks are reported as skipped rather than required,
until each feature has its macOS version.
"""
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QFont, QImage, QPainter

from . import __version__

OCR_PHRASE = "SnipForge self test"


def _check(results: dict, name: str, fn, required: bool = True) -> None:
    try:
        results[name] = {"ok": True, "required": required, "detail": fn()}
    except Exception as exc:  # report every failure, never stop early
        results[name] = {"ok": False, "required": required, "detail": f"{type(exc).__name__}: {exc}",
                         "trace": traceback.format_exc(limit=3)}


def _editor() -> str:
    from .editor.window import EditorWindow
    from .library import Library

    image = QImage(320, 200, QImage.Format_ARGB32)
    image.fill(QColor("white"))
    library = Library(Path(tempfile.mkdtemp(prefix="snipforge-selftest-")))
    window = EditorWindow(image, library, library.create(image))
    canvas = window.canvas
    canvas.set_tool("arrow")
    canvas._begin_create(QPointF(10, 10))
    canvas._update_create(canvas._drag, QPointF(200, 120), False)
    canvas._finish_create(canvas._drag)
    canvas._drag = None
    rendered = window.final_image()
    window.close()
    if rendered.size() != image.size():
        raise RuntimeError(f"render size {rendered.size()} != {image.size()}")
    return f"{len(canvas.annotations())} annotation(s) rendered"


def _library() -> str:
    """The Library window, where SnipForge opens, builds and lists a snip."""
    from .home import HomeWindow
    from .library import Library

    image = QImage(320, 200, QImage.Format_ARGB32)
    image.fill(QColor("white"))
    library = Library(Path(tempfile.mkdtemp(prefix="snipforge-selftest-")))
    library.create(image)
    window = HomeWindow(library, lambda: None, lambda _entry_id: None)
    try:
        window.refresh()
        shown = window.grid.model().card_count()
    finally:
        window.close()
    if shown != 1:
        raise RuntimeError(f"the Library lists {shown} snip(s), expected 1")
    return "Library window lists snips"


def _not_on_this_platform() -> str:
    return f"skipped: Windows only for now ({sys.platform}, see docs/MAC-PLAN.md)"


def _theme() -> str:
    """Theme packs load and the app-wide stylesheet builds and applies (restored afterwards)."""
    from PySide6.QtWidgets import QApplication

    from .theme.manager import ThemeManager

    app = QApplication.instance()
    before = (app.styleSheet(), app.palette())
    manager = ThemeManager(app)
    try:
        manager.apply()
        if not app.styleSheet():
            raise RuntimeError("the theme stylesheet is empty")
        accents = ", ".join(a.id for a in manager.accents())
        return f"{manager.theme.mode} theme applies; accents: {accents}"
    finally:
        manager.close()
        app.setStyleSheet(before[0])
        app.setPalette(before[1])


def _icons() -> str:
    from .editor.stamps import STAMPS, stamp_icon
    from .icons import icon

    missing = [n for n in ("arrow", "crop", "share") if icon(n).isNull()]
    missing += [n for n in STAMPS if stamp_icon(n).isNull()]
    if missing:
        raise RuntimeError(f"icons failed to render: {missing}")
    return "SVG icons render"


def _ocr() -> str:
    # Import the WinRT pieces directly: a packaging mistake must fail here, not be reported as
    # "OCR unavailable". Only a missing Windows language pack counts as a skip.
    import winrt.windows.graphics.imaging  # noqa: F401
    import winrt.windows.media.ocr  # noqa: F401
    import winrt.windows.storage.streams  # noqa: F401

    from . import ocr

    image = QImage(560, 90, QImage.Format_ARGB32)
    image.fill(QColor("white"))
    p = QPainter(image)
    p.setFont(QFont("Segoe UI", 20))
    p.setPen(QColor("black"))
    p.drawText(16, 58, OCR_PHRASE)
    p.end()
    try:
        text = ocr.recognize(image).text
    except ocr.OcrUnavailable as exc:
        return f"skipped: {exc}"
    if "self" not in text.lower():
        raise RuntimeError(f"OCR read {text!r}")
    return f"read {text!r}"


def _search_index() -> str:
    """Library search reads snips with Windows OCR on its background thread (winrt off the UI thread)."""
    import time

    from PySide6.QtWidgets import QApplication

    from . import ocr
    from .indexer import TextIndexer
    from .library import Library

    image = QImage(560, 90, QImage.Format_ARGB32)
    image.fill(QColor("white"))
    p = QPainter(image)
    p.setFont(QFont("Segoe UI", 20))
    p.setPen(QColor("black"))
    p.drawText(16, 58, OCR_PHRASE)
    p.end()
    try:
        ocr.recognize(QImage(40, 20, QImage.Format_ARGB32))
    except ocr.OcrUnavailable as exc:
        return f"skipped: {exc}"
    library = Library(Path(tempfile.mkdtemp(prefix="snipforge-selftest-")))
    entry = library.create(image)
    indexer = TextIndexer(library)
    indexer.index(entry)
    deadline = time.monotonic() + 20
    while not library.meta(entry).indexed and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.05)
    indexer.close()
    text = library.meta(entry).text
    if "self" not in text.lower():
        raise RuntimeError(f"the search index read {text!r}")
    return f"background OCR read {text!r}"


def _windows() -> str:
    from .winrects import window_infos

    found = window_infos()
    apps = sorted({w.app for w in found if w.app})
    return f"{len(found)} window(s); apps: {', '.join(apps[:6]) or 'none named'}"


def _hotkey() -> str:
    from .hotkey import HotkeyListener

    listener = HotkeyListener()
    ok = listener.start()
    listener.stop()
    if not ok:
        raise RuntimeError("keyboard hook could not be installed")
    return "keyboard hook installs and uninstalls"


def _share() -> str:
    from . import share

    image = QImage(40, 40, QImage.Format_ARGB32)
    image.fill(QColor("white"))
    path = share.export(image, Path(tempfile.mkdtemp(prefix="snipforge-selftest-")))
    return f"Windows share command offered: {share.has_shell_verb(path, share.WINDOWS_SHARE_VERB)}"


def run(report_path: str) -> int:
    windows = sys.platform == "win32"
    results: dict = {}
    _check(results, "theme", _theme)
    _check(results, "editor", _editor)
    _check(results, "library", _library)
    _check(results, "icons", _icons)
    _check(results, "ocr", _ocr if windows else _not_on_this_platform, required=windows)
    _check(results, "search_index", _search_index if windows else _not_on_this_platform, required=windows)
    _check(results, "windows", _windows, required=False)
    _check(results, "hotkey", _hotkey if windows else _not_on_this_platform, required=windows)
    _check(results, "share", _share, required=False)
    passed = all(r["ok"] for r in results.values() if r["required"])
    report = {"version": __version__, "platform": sys.platform, "passed": passed, "checks": results}
    try:
        Path(report_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError:
        return 2  # can't write the report: treat as a failed self-test
    return 0 if passed else 1
