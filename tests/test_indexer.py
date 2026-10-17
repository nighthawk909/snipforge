"""Background text recognition for Library search (OCR replaced by a fake; the desktop suite
runs the real Windows OCR on the worker thread)."""
import threading
import time

from PySide6.QtWidgets import QApplication

from snipforge import ocr
from snipforge.indexer import TextIndexer
from snipforge.library import Library


def wait_until(condition, seconds=5.0):
    end = time.time() + seconds
    while not condition() and time.time() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    QApplication.processEvents()
    return condition()


def test_indexes_new_snips_on_a_background_thread(qapp, tmp_path, white_image):
    lib = Library(tmp_path)
    entry = lib.create(white_image)
    threads, done = [], []
    indexer = TextIndexer(lib, lambda image: threads.append(threading.current_thread().name) or "Gross margin FY26")
    indexer.indexed.connect(done.append)
    assert indexer.index(entry)
    assert wait_until(lambda: done == [entry.id])
    meta = lib.meta(entry)
    assert meta.indexed and meta.text == "Gross margin FY26"
    assert threads and threads[0] != threading.main_thread().name
    indexer.close()


def test_backfill_skips_indexed_snips_and_respects_the_limit(qapp, tmp_path, white_image):
    lib = Library(tmp_path)
    entries = [lib.create(white_image) for _ in range(4)]
    lib.update_meta(entries[0], indexed=True)
    gate = threading.Event()
    indexer = TextIndexer(lib, lambda image: gate.wait(5) and "")
    assert indexer.backfill(entries, limit=2) == 2
    assert not indexer.index(entries[1])  # already queued
    gate.set()
    assert wait_until(lambda: indexer.pending() == 0)
    assert [lib.meta(e).indexed for e in entries] == [True, True, True, False]
    indexer.close()


def test_missing_ocr_language_marks_snips_and_stops_trying(qapp, tmp_path, white_image):
    lib = Library(tmp_path)
    first, second = lib.create(white_image), lib.create(white_image)

    def unavailable(_image):
        raise ocr.OcrUnavailable("No OCR language pack is installed in Windows.")

    indexer = TextIndexer(lib, unavailable)
    indexer.index(first)
    assert wait_until(lambda: lib.meta(first).indexed)
    assert not indexer.index(second)
    indexer.close()


def test_errors_and_deleted_snips_never_escape(qapp, tmp_path, white_image, monkeypatch):
    lib = Library(tmp_path)
    entry = lib.create(white_image)

    def boom(_image):
        raise RuntimeError("WinRT hiccup")

    indexer = TextIndexer(lib, boom)
    indexer.index(entry)
    assert wait_until(lambda: indexer.pending() == 0)
    assert not lib.meta(entry).indexed  # tried again next time
    indexer.close()
    assert not indexer.index(entry)  # closed
