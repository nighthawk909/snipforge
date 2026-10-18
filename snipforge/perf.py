"""SnipForge's speed and footprint, measured: start-up, snip to editor, idle memory and CPU, and a
Library with more than a thousand snips. scripts/measure_performance.py prints them; the packaged
self-test (snipforge/selftest.py) checks the ones that hold on any machine against BUDGETS.

Everything here works on a made-up Library in a temporary folder and never installs the keyboard
hook, shows the tray icon or goes online.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

MB = 1024 * 1024
# Limits a change must stay within (docs/PERFORMANCE.md has today's numbers). Timings have room for a
# slow CI machine: they catch a change that makes something several times slower, not noise.
BUDGETS = {
    "startup_modules": 300,  # modules loaded by the time SnipForge is up, on Windows (260 today)
    "startup_s": 3.0,  # import to the Library painted (0.3 s today)
    "startup_memory_mb": 150,  # private bytes on Windows: 39 MB today (offscreen; more with a display)
    "snip_to_editor_s": 1.5,  # a 1920 × 1080 snip saved and its editor painted (0.18 s today)
    "library_open_s": 3.0,  # the Library window over 1,000 snips (0.4 s today)
    "library_refresh_s": 1.0,  # opening it again (0.05 s today)
}


def memory_mb() -> float:
    """This process's memory: private bytes on Windows, the peak resident size elsewhere."""
    if sys.platform == "win32":
        from ._perf_win32 import private_bytes
        return round(private_bytes() / MB, 1)
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (MB if sys.platform == "darwin" else 1024), 1)  # bytes on macOS, KB on Linux


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _scratch(prefix: str) -> Path:
    """A temporary folder that's removed when the process ends."""
    import atexit
    folder = Path(tempfile.mkdtemp(prefix=prefix))
    atexit.register(shutil.rmtree, folder, True)
    return folder


def pump(seconds: float) -> None:
    from PySide6.QtWidgets import QApplication
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def _quiet_forge(library_root: Path):
    """SnipForge as it starts, minus the keyboard hook, the shortcuts of your own and update checks."""
    from . import app as app_module
    from .library import Library

    app_module.HotkeyListener.start = lambda self: True
    app_module.HotkeyListener.stop = lambda self: None
    app_module.UpdateFlow.start = lambda self: None
    return app_module.SnipForgeApp(_app(), Library(library_root))


def startup() -> dict:
    """From importing SnipForge to the Library window painted, as a plain launch does."""
    started = time.perf_counter()
    import snipforge.app  # noqa: F401 - what launching imports
    imported = time.perf_counter()
    app = _app()
    from . import theme
    theme.install(app)
    forge = _quiet_forge(_scratch("snipforge-perf-"))
    ready = time.perf_counter()
    forge.show_home()
    forge._home.grab()
    shown = time.perf_counter()
    return {"import_s": round(imported - started, 3), "app_ready_s": round(ready - started, 3),
            "startup_s": round(shown - started, 3), "modules": len(sys.modules),
            "heavy_modules": sorted(heavy_modules()), "memory_mb": memory_mb()}


HEAVY = ("PySide6.QtMultimedia", "PIL", "asyncio", "winrt.windows.media.ocr", "win32com.client",
         "snipforge.video.recorder", "snipforge.video.editor_window", "snipforge.editor.window",
         "snipforge.diagnostics", "snipforge.report_dialog")


def heavy_modules() -> set[str]:
    """Big modules loaded so far that a plain start shouldn't need."""
    return {name for name in HEAVY if name in sys.modules}


def sample_image(width: int = 1920, height: int = 1080):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QImage, QPainter

    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor("#f3f4f6"))
    p = QPainter(image)
    for n in range(0, height, 40):
        p.fillRect(QRect(40, n + 8, width - 80, 24), QColor("#3366cc" if n % 120 else "#e11d48"))
    p.end()
    return image


def snip_to_editor() -> dict:
    """A 1920 × 1080 snip, from the capture finishing to the editor painted (saved in the Library first)."""
    app = _app()
    from . import theme
    theme.install(app)
    forge = _quiet_forge(_scratch("snipforge-perf-"))
    forge.indexer._recognize = lambda image: ""  # text recognition runs on its own thread anyway
    from .app import EDITOR_PRELOAD_MS
    pump(EDITOR_PRELOAD_MS / 1000 + 0.5)  # as in real use: the first snip comes after start-up
    image = sample_image()
    timings = []
    for _ in range(3):
        started = time.perf_counter()
        forge._on_captured(image)
        editor = forge._windows[-1]
        editor.grab()
        timings.append(time.perf_counter() - started)
        editor.close()
        pump(0.1)
    return {"snip_to_editor_s": round(sorted(timings)[1], 3), "first_s": round(timings[0], 3),
            "memory_mb": memory_mb()}


def open_editor_s(runs: int = 3) -> float:
    """A 1920 × 1080 snip saved in a Library and its editor painted (the median of `runs`), without
    the rest of the app: what the packaged self-test times."""
    from .editor.window import EditorWindow
    from .library import Library

    library = Library(_scratch("snipforge-perf-"))
    image = sample_image()
    timings = []
    for _ in range(runs):
        started = time.perf_counter()
        window = EditorWindow(image, library, library.create(image))
        window.show()
        window.grab()
        timings.append(time.perf_counter() - started)
        window.close()
        window.deleteLater()
    return round(sorted(timings)[len(timings) // 2], 3)


def idle(seconds: float = 10.0) -> dict:
    """SnipForge waiting in the tray: memory, and CPU time used over `seconds` of doing nothing."""
    app = _app()
    from . import theme
    theme.install(app)
    _quiet_forge(_scratch("snipforge-perf-"))
    pump(2.0)  # start-up work settles
    cpu, wall = time.process_time(), time.perf_counter()
    pump_idle(seconds)
    used = time.process_time() - cpu
    return {"idle_memory_mb": memory_mb(), "idle_cpu_percent": round(100 * used / (time.perf_counter() - wall), 2)}


def pump_idle(seconds: float) -> None:
    """Run the event loop for `seconds` without spinning: as the app does when nothing happens."""
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()


def make_library(root: Path, snips: int) -> None:
    """A Library of `snips` snips, one a minute going back in time, from one real snip copied."""
    from .library import Library

    library = Library(root)
    first = library.create(sample_image(1280, 720))
    library.update_meta(first, app="Contoso Analytics", tags=["report"])
    now = dt.datetime(2026, 10, 18, 12, 0)
    for n in range(1, snips):
        stamp = (now - dt.timedelta(minutes=n)).strftime("%Y%m%d-%H%M%S-%f")[:19]
        shutil.copytree(first.path, root / stamp)
    (first.path / "meta.json").write_text(json.dumps({"app": "Contoso Analytics", "tags": ["report"]}),
                                          encoding="utf-8")


def module_budget() -> int | None:
    """The start-up module budget here: Windows'. The Mac's PyObjC brings modules of its own, so
    there only the heavy ones are checked until the Mac port measures it."""
    return BUDGETS["startup_modules"] if sys.platform == "win32" else None


def memory_budget() -> float | None:
    """The start-up memory budget here: Windows' private bytes. Elsewhere memory_mb() is the peak
    resident size, a different measure, so it's reported, not budgeted, until the Mac port sets one."""
    return BUDGETS["startup_memory_mb"] if sys.platform == "win32" else None


def over_budget(results: dict) -> list[str]:
    """What in `results` (measurements named as in BUDGETS) is over its budget, in words."""
    return [f"{name} is {value} (budget {BUDGETS[name]})" for name, value in results.items()
            if name in BUDGETS and value > BUDGETS[name]]


def big_library(snips: int = 1200) -> dict:
    """The Library window over `snips` snips: opening it, painting it, and its memory."""
    app = _app()
    from . import theme
    theme.install(app)
    root = _scratch("snipforge-perf-lib-")
    make_library(root, snips)
    for path in root.rglob("*"):  # as a Library on disk for a while is: antivirus has seen every file
        if path.is_file():
            path.read_bytes()
    before = memory_mb()
    from .home import HomeWindow
    from .library import Library

    started = time.perf_counter()
    home = HomeWindow(Library(root), lambda: None, lambda _id: None)
    home.resize(1280, 800)
    home.show()
    home.grab()
    shown = time.perf_counter()
    pump(1.5)  # previews load in the background
    home.grab()
    settled = time.perf_counter()
    started_refresh = time.perf_counter()
    home.refresh()
    home.grab()
    refreshed = time.perf_counter()
    home.close()
    return {"snips": snips, "open_s": round(shown - started, 3), "previews_settled_s": round(settled - started, 3),
            "refresh_s": round(refreshed - started_refresh, 3), "memory_mb": memory_mb(),
            "memory_added_mb": round(memory_mb() - before, 1)}
