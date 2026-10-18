"""Small pieces of the video editor window: the work it hands to background tasks, names for the
files it writes, its play and pause icons, and showing a saved file in Explorer."""
from __future__ import annotations

import contextlib
import itertools
import logging
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap, QPolygonF

from . import frames

log = logging.getLogger(__name__)

THUMBNAILS = 24  # across the whole video; the strip shows the nearest one in each tile
THUMB_WIDTH = 192  # pixels: sharp on a 2x screen at the strip's height


def next_free_name(original: Path, label: str | None, suffix: str) -> Path:
    """A name next to `original` that isn't taken: "Clip (trimmed).mp4", "Clip (trimmed 2).mp4", or
    with no label "Clip.gif", "Clip (2).gif". Never the original itself."""
    for n in itertools.count(1):
        if label:
            tag = f" ({label})" if n == 1 else f" ({label} {n})"
        else:
            tag = "" if n == 1 else f" ({n})"
        candidate = original.with_name(f"{original.stem}{tag}{suffix}")
        if candidate != original and not candidate.exists():
            return candidate
    raise AssertionError("unreachable")


def thumbnail_work(path: Path, length: float):
    """Work for a Task: hands over (seconds, image) thumbnails spread along the video."""
    def work(task) -> None:
        with contextlib.closing(frames.read(path, fps=THUMBNAILS / max(length, 0.001), width=THUMB_WIDTH)) as reader:
            for seconds, image in reader:
                task.check()
                task.emit((seconds, image))
    return work


def still_work(path: Path, index: int, start: float, end: float):
    """Work for a Task: the exact frame `index` (shown from `start` to `end`), as (index, image)."""
    def work(task):
        with contextlib.closing(frames.read(path, start=start, end=end)) as reader:
            found = next(reader, None)
        if found is None:
            raise ValueError(f"No frame at {start:.2f} s")
        return index, found[1]
    return work


def glyph(kind: str, colour: str, size: int = 32) -> QIcon:
    """SnipForge's own play and pause symbols, drawn in `colour`."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(colour))
    s = size / 32
    if kind == "play":
        p.drawPolygon(QPolygonF([QPointF(10 * s, 6 * s), QPointF(26 * s, 16 * s), QPointF(10 * s, 26 * s)]))
    else:
        p.drawRoundedRect(QRectF(8 * s, 6 * s, 6 * s, 20 * s), 1.5 * s, 1.5 * s)
        p.drawRoundedRect(QRectF(18 * s, 6 * s, 6 * s, 20 * s), 1.5 * s, 1.5 * s)
    p.end()
    return QIcon(pixmap)


def reveal(path: Path) -> None:
    """Show `path` in Explorer (selected) or Finder (its folder)."""
    try:
        if sys.platform == "win32":
            # A string, not a list: Explorer wants /select,"path" exactly, which list quoting changes.
            subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')  # no shell; a file we just wrote
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
    except OSError as exc:
        log.info("Couldn't show %s: %s", path, exc)
