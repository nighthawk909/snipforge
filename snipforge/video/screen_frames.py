"""The recorded area as numbered frames at a steady rate, made on threads of their own.

Two threads, so that a slow screen never holds up the frames:

    screen-watch   waits on DXGI for the screen to change and keeps the newest image
    screen-frames  when frame n is due (n/fps seconds in, by the recording's Clock, so pauses are left
                   out) copies the newest image, draws the pointer, its highlight and click rings into
                   it, and queues it for the encoder

A still screen just repeats its last image, and so does a screen that's slow to answer: a software
graphics driver (GitHub's runners), a busy GPU or a display turned off can make each wait on DXGI
take a second, which once left the frame thread making 3 frames in 3 s. Now the picture may lag,
but the video stays in time and complete.

If the frame thread falls more than LATE_FRAMES behind (a busy moment), it skips ahead to the frame
that's due rather than catching up with frames already late: the encoder places every frame by its
number, so the video stays in time. If the queue is full (the encoder is behind), the new frame is
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
WATCH_MS = 100  # how long one wait on the screen may last before the watcher checks it should stop
NAP = 0.05  # the longest the frame thread sleeps at once (it checks for stop in between)


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
        self.slowest_gap = self.slowest_draw = 0.0  # seconds: where a starved frame thread's time went
        self.protected_content = False
        self.error: Exception | None = None
        self.overlay_error: Exception | None = None  # drawing the pointer failed: frames go on without it
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._threads: list[threading.Thread] = []
        self._screen = None
        self._latest: QImage | None = None
        self._lock = threading.Lock()  # around _latest, swapped by one thread and copied by the other

    def start(self) -> None:
        """Open the screen; raises whatever stops it from being recorded."""
        watcher = threading.Thread(target=self._watch_screen, name="screen-watch", daemon=True)
        self._threads = [watcher]
        watcher.start()
        if not self._ready.wait(START_TIMEOUT):
            self.stop()
            raise OSError("the screen took too long to open")
        if self.error:
            raise self.error
        maker = threading.Thread(target=self._make_frames_safely, name="screen-frames", daemon=True)
        self._threads.append(maker)
        maker.start()

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(5)

    def take(self) -> list[tuple[int, QImage]]:
        """The frames made since the last call, oldest first."""
        made = []
        while True:
            try:
                made.append(self.frames.get_nowait())
            except queue.Empty:
                return made

    # --- the screen-watch thread ------------------------------------------------------------------

    def _watch_screen(self) -> None:
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
            while not self._stop.is_set():
                self._watch(WATCH_MS)
        except Exception as exc:
            log.exception("Watching the screen failed")
            self.error = exc
        finally:
            self._close_screen()

    def _watch(self, timeout_ms: int) -> None:
        got = self._screen.grab(timeout_ms)
        self.protected_content = bool(getattr(self._screen, "protected_content", False))
        if got:
            data, stride = got
            width, height = self.area[2], self.area[3]
            image = QImage(data, width, height, stride, QImage.Format.Format_RGB32).copy()
            with self._lock:
                self._latest = image
            self.screen_updates += 1

    def _close_screen(self) -> None:
        if self._screen is not None:
            self.reopens = getattr(self._screen, "reopens", 0)
            try:
                self._screen.close()
            except Exception:
                log.exception("Closing the screen duplication failed")
            self._screen = None

    # --- the screen-frames thread -----------------------------------------------------------------

    def _make_frames_safely(self) -> None:
        try:
            self._make_frames()
        except Exception as exc:
            log.exception("Making screen frames failed")
            self.error = exc

    def _make_frames(self) -> None:
        index, period = 0, 1 / self.fps
        last = self.now()
        while not self._stop.is_set() and self.error is None:
            now = self.now()
            self.slowest_gap, last = max(self.slowest_gap, now - last), now
            media = self.clock.media_time(now)
            if media is None or media < index * period:  # paused, or not due yet
                time.sleep(min(NAP, period / 2 if media is None else index * period - media))
                continue
            behind = int(media / period) - index
            if index and behind > LATE_FRAMES:
                self.late_skipped += behind
                index += behind
            with self._lock:
                frame = self._latest.copy()
            self._draw_pointer(frame, now)
            self.slowest_draw = max(self.slowest_draw, self.now() - now)
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
