"""Finds the words in each snip with Windows' built-in OCR (offline), so Library search can match
them. One snip at a time on a background thread, so capturing and editing never wait on it."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from . import ocr
from .library import Entry, Library

log = logging.getLogger(__name__)
BACKFILL_MAX = 300  # older snips indexed per session, so a huge library doesn't keep the CPU busy


def ocr_text(image: QImage) -> str:
    return " ".join(line.text for line in ocr.recognize(image).lines)


class TextIndexer(QObject):
    indexed = Signal(str)  # entry id; delivered on the UI thread

    def __init__(self, library: Library, recognize=ocr_text, parent=None) -> None:
        super().__init__(parent)
        self._library = library
        self._recognize = recognize
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="snipforge-ocr")
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._closed = False
        self._unavailable = False

    def index(self, entry: Entry) -> bool:
        """Queue one snip. Returns False if it's already queued or indexing is off."""
        with self._lock:
            if self._closed or self._unavailable or entry.id in self._pending:
                return False
            self._pending.add(entry.id)
        try:
            self._pool.submit(self._work, entry)
        except RuntimeError:  # shut down meanwhile
            with self._lock:
                self._pending.discard(entry.id)
            return False
        return True

    def backfill(self, entries: list[Entry], limit: int = BACKFILL_MAX) -> int:
        """Queue snips that have never been indexed (e.g. taken before this version)."""
        queued = 0
        for entry in entries:
            if queued >= limit:
                break
            if not self._library.meta(entry).indexed and self.index(entry):
                queued += 1
        return queued

    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _work(self, entry: Entry) -> None:
        try:
            image = QImage(str(entry.original_path))
            text = self._recognize(image) if not image.isNull() else ""
        except ocr.OcrUnavailable as exc:  # no OCR language: stop trying for this session
            log.info("Library search can't read text in snips: %s", exc)
            with self._lock:
                self._unavailable = True
            text = ""
        except Exception:
            log.exception("Couldn't read the text in snip %s", entry.id)
            self._done(entry)
            return
        try:
            self._library.update_meta(entry, text=text, indexed=True)
        except OSError as exc:  # e.g. the snip was deleted meanwhile
            log.info("Couldn't save the text of snip %s: %s", entry.id, exc)
            self._done(entry)
            return
        self._done(entry)
        self.indexed.emit(entry.id)

    def _done(self, entry: Entry) -> None:
        with self._lock:
            self._pending.discard(entry.id)
