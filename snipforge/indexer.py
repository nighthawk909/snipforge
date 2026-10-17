"""Finds the words in each snip with Windows' built-in OCR (offline), so Library search can match
them. One snip at a time on a background thread, so capturing and editing never wait on it.

It reads the finished snip (rendered.png when the snip was edited), so words you blurred or cropped
away aren't searchable; saving an edit clears the old text until it's read again."""
from __future__ import annotations

import logging
import queue
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from . import ocr
from .library import Entry, Library

log = logging.getLogger(__name__)
SESSION_BACKFILL_MAX = 300  # older snips read per app session, so a huge library doesn't keep the CPU busy
_STOP = object()


def ocr_text(image: QImage) -> str:
    return " ".join(line.text for line in ocr.recognize(image).lines)


class TextIndexer(QObject):
    indexed = Signal(str)  # entry id; delivered on the UI thread

    def __init__(self, library: Library, recognize=ocr_text, parent=None) -> None:
        super().__init__(parent)
        self._library = library
        self._recognize = recognize
        self._queue: queue.Queue = queue.Queue()
        self._pending: set[str] = set()
        self._tried: set[str] = set()   # attempted this session (failures aren't retried until next start)
        self._lock = threading.Lock()
        self._closed = False
        self._unavailable = False
        self._backfilled = 0
        self.enabled = True
        # A daemon thread: a hung OCR call can never keep SnipForge (or its updater) from exiting.
        self._thread = threading.Thread(target=self._run, name="snipforge-ocr", daemon=True)
        self._thread.start()

    def index(self, entry: Entry, again: bool = False) -> bool:
        """Queue one snip. `again` re-reads a snip already tried this session (after an edit).
        Returns False if it's already queued or reading is off/unavailable."""
        with self._lock:
            if self._closed or self._unavailable or not self.enabled or entry.id in self._pending:
                return False
            if entry.id in self._tried and not again:
                return False
            self._pending.add(entry.id)
            self._tried.add(entry.id)
        self._queue.put(entry)
        return True

    def backfill(self, entries: list[Entry]) -> int:
        """Queue snips that have never been read (e.g. taken before this version). The caller passes
        only those; at most SESSION_BACKFILL_MAX per app session."""
        queued = 0
        for entry in entries:
            if self._backfilled >= SESSION_BACKFILL_MAX:
                break
            if self.index(entry):
                self._backfilled += 1
                queued += 1
        return queued

    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def available(self) -> bool:
        return not self._unavailable

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._queue.put(_STOP)

    # ------------------------------------------------------------------ worker thread
    def _run(self) -> None:
        while True:
            entry = self._queue.get()
            if entry is _STOP:
                return
            try:
                self._work(entry)
            finally:
                with self._lock:
                    self._pending.discard(entry.id)

    def _work(self, entry: Entry) -> None:
        with self._lock:
            if self._closed or self._unavailable:
                return
        try:
            image = QImage(str(entry.rendered_path))
            text = self._recognize(image) if not image.isNull() else ""
        except ocr.OcrUnavailable as exc:
            # No OCR language (or a broken build): leave the snip unread so it's read once that's fixed.
            log.info("Library search can't read text in snips: %s", exc)
            with self._lock:
                self._unavailable = True
            return
        except Exception:
            log.exception("Couldn't read the text in snip %s; trying again next time", entry.id)
            return
        with self._lock:
            if self._closed:
                return
        try:
            self._library.update_meta(entry, text=text, indexed=True)
        except (OSError, ValueError) as exc:  # e.g. deleted meanwhile, or the file is briefly locked
            log.info("Couldn't save the text of snip %s: %s", entry.id, exc)
            return
        self.indexed.emit(entry.id)
