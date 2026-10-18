"""The recorded area as numbered frames at a steady rate, made on a thread of their own.

Frame n is due n/fps seconds into the recording (by the recording's Clock, so pauses are left out).
Between frames the thread waits on DXGI for the screen to change and keeps the newest image; when a
frame is due it copies that image, draws the pointer, its highlight and click rings into it, and
queues it for the encoder. A still screen just repeats its last image.

If the thread falls more than LATE_FRAMES behind (a busy moment), it skips ahead to the frame that's
due rather than catching up with frames already late: the encoder places every frame by its number,
so the video stays in time. If the queue is full (the encoder thread is behind), the new frame is
dropped. Both are counted.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable

from PySide6.QtGui import QImage

from .clock import Clock

log = logging.getLogger(__name__)

QUEUE_SIZE = 8
LATE_FRAMES = 3
START_TIMEOUT = 5.0


class ScreenFrames:
    """Makes frames of `area` (x, y, width, height in physical desktop pixels) at `fps`."""

    def __init__(self, area: tuple[int, int, int, int], fps: int, clock: Clock, overlay, *,
                 open_screen: Callable | None = None, first_image: Callable | None = None,
                 queue_size: int = QUEUE_SIZE, now: Callable[[], float] = time.perf_counter) -> None:
        from . import dxgi, gdi

        self.area, self.fps, self.clock, self.overlay, self.now = tuple(area), fps, clock, overlay, now
        self._open_screen = open_screen or (lambda a: dxgi.AreaDuplicator(*a))
        self._first_image = first_image or gdi.grab_area
        self.frames: queue.Queue = queue.Queue(maxsize=queue_size)
        self.made = self.late_skipped = self.queue_dropped = self.screen_updates = self.reopens = 0
        self.protected_content = False
        self.error: Exception | None = None
        self.overlay_error: Exception | None = None  # drawing the pointer failed: frames go on without it
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._screen = None
        self._latest: QImage | None = None

    def start(self) -> None:
        """Open the screen; raises whatever stops it from being recorded."""
        self._thread = threading.Thread(target=self._run, name="screen-frames", daemon=True)
        self._thread.start()
        if not self._ready.wait(START_TIMEOUT):
            self.stop()
            raise OSError("the screen took too long to open")
        if self.error:
            raise self.error

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(5)

    def take(self) -> list[tuple[int, QImage]]:
        """The frames made since the last call, oldest first."""
        made = []
        while True:
            try:
                made.append(self.frames.get_nowait())
            except queue.Empty:
                return made

    def _run(self) -> None:
        try:
            self._screen = self._open_screen(self.area)
            self._latest = self._first_image(self.area)
        except Exception as exc:  # start() raises it on the caller's thread
            self.error = exc
            self._close_screen()
            return
        finally:
            self._ready.set()
        try:
            self._make_frames()
        except Exception as exc:
            log.exception("Making screen frames failed")
            self.error = exc
        finally:
            self._close_screen()

    def _make_frames(self) -> None:
        index, period = 0, 1 / self.fps
        while not self._stop.is_set():
            now = self.now()
            media = self.clock.media_time(now)
            if media is None or media < index * period:  # paused, or not due yet: watch the screen
                wait = period / 2 if media is None else min(period, index * period - media)
                self._watch(wait)
                continue
            behind = int(media / period) - index
            if index and behind > LATE_FRAMES:
                self.late_skipped += behind
                index += behind
            frame = self._latest.copy()
            self._draw_pointer(frame, now)
            try:
                self.frames.put_nowait((index, frame))
                self.made += 1
            except queue.Full:
                self.queue_dropped += 1
            index += 1

    def _draw_pointer(self, frame: QImage, now: float) -> None:
        if self.overlay_error is None:
            try:
                self.overlay.draw(frame, now)
            except Exception as exc:  # the recording matters more than the pointer in it
                log.exception("Drawing the pointer failed; recording without it")
                self.overlay_error = exc

    def _watch(self, seconds: float) -> None:
        got = self._screen.grab(max(1, int(seconds * 1000)))
        self.protected_content = bool(getattr(self._screen, "protected_content", False))
        if got:
            data, stride = got
            width, height = self.area[2], self.area[3]
            self._latest = QImage(data, width, height, stride, QImage.Format.Format_RGB32).copy()
            self.screen_updates += 1

    def _close_screen(self) -> None:
        if self._screen is not None:
            self.reopens = getattr(self._screen, "reopens", 0)
            try:
                self._screen.close()
            except Exception:
                log.exception("Closing the screen duplication failed")
            self._screen = None
