"""Small pieces of the video editor window: the work it hands to background tasks (thumbnails and
exact still frames), names for the files it writes, its play and pause icons, and showing a saved
file in Explorer."""
from __future__ import annotations

import contextlib
import itertools
import logging
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QImage, QPainter, QPixmap, QPolygonF

from . import frames
from .tasks import Task
from .timeline import FrameTimes

log = logging.getLogger(__name__)

THUMBNAILS = 24  # across the whole video; the strip shows the nearest one in each tile
THUMB_WIDTH = 192  # pixels: sharp on a 2x screen at the strip's height
READ_THROUGH_UP_TO = 30.0  # seconds: about where decoding it all costs as much as 24 separate jumps


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
    """Work for a Task: hands over (seconds, image) thumbnails spread along the video.

    A short video is read straight through. A longer one gets one frame read at each place (a jump
    to the keyframe before it and at most a few seconds of decoding), so a long recording isn't
    decoded whole, and a cancel is noticed between thumbnails."""
    def work(task) -> None:
        if length <= READ_THROUGH_UP_TO:
            _thumbnails_reading_through(task, path, length)
        else:
            _thumbnails_jumping(task, path, length)
    return work


def _thumbnails_reading_through(task, path: Path, length: float) -> None:
    fps = THUMBNAILS / max(length, 0.001)
    with contextlib.closing(frames.read(path, fps=fps, width=THUMB_WIDTH)) as reader:
        for item in reader:
            task.check()
            task.emit(item)


def _thumbnails_jumping(task, path: Path, length: float) -> None:
    for n in range(THUMBNAILS):
        task.check()
        at = (n + 0.5) * length / THUMBNAILS  # the middle of each slice of the video
        with contextlib.closing(frames.read(path, width=THUMB_WIDTH, start=at)) as reader:
            found = next(reader, None)
        if found is not None:
            task.emit(found)


def still_work(path: Path, index: int, start: float, end: float):
    """Work for a Task: the exact frame `index` (shown from `start` to `end`), as (index, image)."""
    def work(task):
        with contextlib.closing(frames.read(path, start=start, end=end)) as reader:
            found = next(reader, None)
        if found is None:
            raise ValueError(f"No frame at {start:.2f} s")
        return index, found[1]
    return work


class StillFrames(QObject):
    """Reads exact frames for the editor's picture while it's paused or scrubbed. One frame is read
    at a time; requests made meanwhile keep only the latest, which is read next, so dragging stays
    quick and ends on the right frame."""

    shown = Signal(int, QImage)
    failed = Signal(str)

    def __init__(self, path: Path, frames: FrameTimes, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._path, self._frames = path, frames
        self.task: Task | None = None
        self._busy = False
        self._reading = self._wanted = 0

    def request(self, index: int) -> None:
        self._wanted = index
        if self._busy:
            return
        self._busy, self._reading = True, index
        work = still_work(self._path, index, self._frames.boundary(index), self._frames.boundary(index + 1))
        self.task = Task(work, done=self._done, failed=self._failed, name="snipforge-video-still").start()

    @property
    def wanted(self) -> int:
        return self._wanted

    def _done(self, result) -> None:
        index, image = result
        if index == self._wanted:
            self.shown.emit(index, image)
        self._next()

    def _failed(self, message: str) -> None:
        self.failed.emit(message)
        self._next()

    def _next(self) -> None:
        self._busy = False
        if self._reading != self._wanted:
            self.request(self._wanted)


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


def reveal(path: Path) -> bool:
    """Show `path` selected in Explorer; False if that couldn't start."""
    try:
        if sys.platform == "win32":
            # Explorer by its full path, so no explorer.exe in the current folder can stand in for it.
            # A string, not a list: Explorer wants /select,"path" exactly, which list quoting changes.
            explorer = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe")
            subprocess.Popen(f'"{explorer}" /select,"{os.path.normpath(path)}"')  # no shell; a file we just wrote
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
    except OSError as exc:
        log.warning("Couldn't show %s: %s", path, exc)
        return False
    return True
