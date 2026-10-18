"""Work the video editor does off the UI thread (thumbnails, still frames, saving, GIF export),
reporting back on the UI thread through Qt signals.

A Task runs one function on its own thread. The function gets the task, so it can report progress,
hand over results as they come (thumbnails), and check whether it was cancelled. Callbacks are
connected as Qt slots: bound methods of a widget stop being called once that widget is deleted.
The window that starts tasks cancels and waits for them when it closes, so none outlives it.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)


class _Signals(QObject):
    progress = Signal(float)
    item = Signal(object)
    done = Signal(object)
    failed = Signal(str)
    cancelled = Signal()


class TaskCancelled(Exception):
    """Raised by Task.check() once the task has been cancelled."""


class Task:
    def __init__(self, work: Callable[[Task], object], *, done=None, failed=None, cancelled=None, progress=None,
                 item=None, name: str = "snipforge-video") -> None:
        self._work = work
        self._cancel = threading.Event()
        self._signals = _Signals()
        for signal, slot in ((self._signals.done, done), (self._signals.failed, failed),
                             (self._signals.cancelled, cancelled), (self._signals.progress, progress),
                             (self._signals.item, item)):
            if slot is not None:
                signal.connect(slot)
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    def start(self) -> Task:
        self._thread.start()
        return self

    @property
    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def is_running(self) -> bool:
        return self._thread.is_alive()

    def cancel(self) -> None:
        self._cancel.set()

    def check(self) -> None:
        """For the work function: stop here if the task was cancelled."""
        if self._cancel.is_set():
            raise TaskCancelled

    def report(self, fraction: float) -> None:
        self._signals.progress.emit(float(fraction))

    def emit(self, item: object) -> None:
        self._signals.item.emit(item)

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for the work to end; True if it has."""
        if self._thread.is_alive():
            self._thread.join(timeout)
        return not self._thread.is_alive()

    def _run(self) -> None:
        try:
            result = self._work(self)
        except TaskCancelled:
            self._signals.cancelled.emit()
        except Exception as exc:  # reported to the window, which says what went wrong
            if self._cancel.is_set():
                self._signals.cancelled.emit()
                return
            log.exception("%s failed", self._thread.name)
            self._signals.failed.emit(str(exc) or type(exc).__name__)
        else:
            if self._cancel.is_set():
                self._signals.cancelled.emit()
            else:
                self._signals.done.emit(result)
