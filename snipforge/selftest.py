"""`SnipForge.exe --self-test <report.json>`: proves a packaged build actually works.

Exercises the pieces that break when packaging goes wrong (Qt plugins, theme, SVG icons, the editor,
Windows OCR, the keyboard hook, Explorer share commands) and writes a JSON report. Exit code 0
means every required check passed. CI runs this against the built exe before publishing.

On a Mac (docs/MAC-PLAN.md) the Windows-only checks are reported as skipped rather than required,
until each feature has its macOS version; the Mac's own (the Screen Recording check, files in
Contents/Resources) are required there.
"""
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage, QPainter

from . import __version__
from .fonts import ui_font

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
    rendered = canvas.render_image()  # not final_image(): that's bigger when Beautify is on
    final = window.final_image()
    window.close()
    if rendered.size() != image.size() or final.isNull():
        raise RuntimeError(f"render size {rendered.size()} != {image.size()}")
    return f"{len(canvas.annotations())} annotation(s) rendered"


def _save_formats() -> str:
    """Save writes every format Settings offers (WebP needs Qt's image-format plugin to be bundled)."""
    from .editor.export import save_image
    from .prefs import FORMATS

    image = QImage(64, 40, QImage.Format_ARGB32)
    image.fill(QColor("#336699"))
    folder = Path(tempfile.mkdtemp(prefix="snipforge-selftest-"))
    for fmt in FORMATS:
        path = folder / f"snip.{fmt}"
        if not save_image(image, path) or path.stat().st_size == 0:
            raise RuntimeError(f"couldn't save a {fmt.upper()}")
        if fmt == "pdf" and not path.read_bytes().startswith(b"%PDF"):
            raise RuntimeError("a saved PDF isn't a PDF")
        if fmt != "pdf" and QImage(str(path)).size() != image.size():
            raise RuntimeError(f"a saved {fmt.upper()} doesn't read back")
    return ", ".join(f.upper() for f in FORMATS) + " saved"


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


def _app_folders() -> str:
    """The license texts are where About and Settings say (next to SnipForge.exe, or in
    SnipForge.app's Contents/Resources), and the theme's cache is in the platform's cache folder."""
    from .bundle import cache_dir, resources_dir
    from .theme.manager import ASSET_DIR

    folder = resources_dir()
    missing = [name for name in ("THIRD_PARTY_NOTICES.md", "licenses") if not (folder / name).exists()]
    if missing:
        raise RuntimeError(f"{' and '.join(missing)} not found in {folder}")
    if cache_dir() not in ASSET_DIR.parents:
        raise RuntimeError(f"the theme cache {ASSET_DIR} isn't in {cache_dir()}")
    return f"license texts in {folder}; cache in {cache_dir()}"


def _fonts() -> str:
    """The interface and fixed-width fonts SnipForge asks for are there (a Mac has no Segoe UI or
    Consolas: snipforge/fonts.py picks its own)."""
    from PySide6.QtGui import QFontInfo

    from .fonts import mono_font

    ui, mono = QFontInfo(ui_font(10)), QFontInfo(mono_font(10))
    if not mono.fixedPitch():
        raise RuntimeError(f"the fixed-width font came out as {mono.family()}, which isn't fixed-width")
    return f"interface: {ui.family()}; fixed-width: {mono.family()}"


def _screen_access() -> str:
    """macOS's Screen Recording check made it into the build (PyObjC's Quartz) and answers. CI can't
    grant the permission, so either answer passes."""
    from . import _screen_access_mac

    allowed = _screen_access_mac.preflight()
    return f"Screen Recording permission: {'allowed' if allowed else 'not allowed yet'}"


def _no_screen_permission() -> str:
    return f"skipped: {sys.platform} needs no permission to capture the screen"


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
    p.setFont(ui_font(20))
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
    p.setFont(ui_font(20))
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


def _video() -> str:
    """Screen recording's encoder made it into the build: Qt Multimedia and its FFmpeg encode a second
    of frames and sound the way recordings are (H.264 + AAC in MP4), and FFmpeg reads the file back."""
    from .video import clip, ffmpeg, support

    reason = support.unsupported_reason()  # a Windows "N" edition: say what to add
    if reason:
        raise RuntimeError(reason)
    folder = Path(tempfile.mkdtemp(prefix="snipforge-selftest-"))
    info = ffmpeg.probe(clip.make_test_clip(folder / "selftest.mp4", seconds=1.0))
    video, audio = info.first("video"), info.first("audio")
    codecs = (video.codec if video else "no video", audio.codec if audio else "no sound")
    if codecs != ("h264", "aac"):
        raise RuntimeError(f"recorded {' + '.join(codecs)}, not h264 + aac")
    if not 0.9 <= (info.duration or 0) <= 1.1:
        raise RuntimeError(f"a 1 s clip reads back as {info.duration} s")
    return f"{info.duration:.2f} s clip: h264 {video.width}x{video.height} + aac {audio.sample_rate} Hz"


def _video_editor() -> str:
    """The video editor made it into the build: it opens a clip, and Pillow (only its core and GIF
    writer are bundled) turns the clip into a GIF that reads back with the right number of frames."""
    from PIL import Image

    from .video import clip, gif
    from .video.editor_window import VideoEditorWindow

    folder = Path(tempfile.mkdtemp(prefix="snipforge-selftest-"))
    source = clip.make_test_clip(folder / "editor.mp4", seconds=1.0)
    window = VideoEditorWindow(source)
    try:
        frames = len(window.timeline.frames)
    finally:
        window.close()
        window.deleteLater()
    with Image.open(gif.export_gif(source, folder / "editor.gif", fps=12, width=160)) as written:
        gif_frames = written.n_frames
    if gif_frames != 12:
        raise RuntimeError(f"a 1 s GIF at 12 frames a second has {gif_frames} frames")
    return f"editor opened a {frames}-frame clip; GIF of {gif_frames} frames"


def _recorder_process() -> str:
    """Recordings are made in a process of their own (`SnipForge.exe --record`): start one and have
    it answer over its channel, as SnipForge does for each recording."""
    from .video.remote_recorder import ping

    return f"the recorder process answered (version {ping(timeout_ms=60_000)})"


def _startup_imports() -> str:
    """Starting SnipForge loaded only what it needs: the editor, recording, text recognition and
    the like load when they're first used (docs/PERFORMANCE.md). First, before other checks load them."""
    from . import perf

    heavy = perf.heavy_modules()
    if heavy:
        raise RuntimeError(f"loaded at start: {', '.join(sorted(heavy))}")
    budget = perf.module_budget()
    if budget is not None and len(sys.modules) > budget:
        raise RuntimeError(f"{len(sys.modules)} modules at start (budget {budget})")
    return f"{len(sys.modules)} modules at start (budget {budget or 'none here'}), none of the heavy ones"


def _speed() -> str:
    """A snip's editor, and the Library window over 1,000 snips, open within their budgets."""
    from . import perf

    editor = perf.open_editor_s()
    library = perf.big_library(1000)
    measured = {"snip_to_editor_s": editor, "library_open_s": library["open_s"],
                "library_refresh_s": library["refresh_s"]}
    problems = perf.over_budget(measured)
    if problems:
        raise RuntimeError("; ".join(problems))
    return ", ".join(f"{name} {value}" for name, value in measured.items())


def run(report_path: str) -> int:
    windows, mac = sys.platform == "win32", sys.platform == "darwin"
    results: dict = {}
    _check(results, "startup_imports", _startup_imports)
    _check(results, "theme", _theme)
    _check(results, "app_folders", _app_folders)
    _check(results, "fonts", _fonts, required=mac)
    _check(results, "screen_access", _screen_access if mac else _no_screen_permission, required=mac)
    _check(results, "editor", _editor)
    _check(results, "save_formats", _save_formats)
    _check(results, "library", _library)
    _check(results, "icons", _icons)
    _check(results, "ocr", _ocr if windows else _not_on_this_platform, required=windows)
    _check(results, "search_index", _search_index if windows else _not_on_this_platform, required=windows)
    _check(results, "windows", _windows, required=False)
    _check(results, "hotkey", _hotkey if windows else _not_on_this_platform, required=windows)
    _check(results, "share", _share, required=False)
    _check(results, "video", _video)
    _check(results, "video_editor", _video_editor)
    _check(results, "recorder_process", _recorder_process, required=windows)
    _check(results, "speed", _speed)
    passed = all(r["ok"] for r in results.values() if r["required"])
    report = {"version": __version__, "platform": sys.platform, "passed": passed, "checks": results}
    try:
        Path(report_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError:
        return 2  # can't write the report: treat as a failed self-test
    return 0 if passed else 1
