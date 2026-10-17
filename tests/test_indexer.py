"""Background text recognition for Library search (OCR replaced by a fake; the desktop suite
runs the real Windows OCR on the worker thread)."""
import threading
import time

from PySide6.QtWidgets import QApplication

from snipforge import indexer as indexer_module
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


def test_indexes_new_snips_on_a_daemon_thread(qapp, tmp_path, white_image):
    lib = Library(tmp_path)
    entry = lib.create(white_image)
    threads, done = [], []
    indexer = TextIndexer(lib, lambda image: threads.append(threading.current_thread()) or "Gross margin FY26")
    indexer.indexed.connect(done.append)
    assert indexer.index(entry)
    assert wait_until(lambda: done == [entry.id])
    meta = lib.meta(entry)
    assert meta.indexed and meta.text == "Gross margin FY26"
    assert threads[0] is not threading.main_thread() and threads[0].daemon
    indexer.close()


def test_backfill_has_a_per_session_limit_and_skips_what_was_tried(qapp, tmp_path, white_image, monkeypatch):
    monkeypatch.setattr(indexer_module, "SESSION_BACKFILL_MAX", 3)
    lib = Library(tmp_path)
    entries = [lib.create(white_image) for _ in range(5)]
    gate = threading.Event()
    indexer = TextIndexer(lib, lambda image: gate.wait(5) and "")
    assert indexer.backfill(entries[:2]) == 2
    assert not indexer.index(entries[1])  # already queued
    assert indexer.backfill(entries) == 1  # the session limit is 3 in all
    gate.set()
    assert wait_until(lambda: indexer.pending() == 0)
    assert [lib.meta(e).indexed for e in entries] == [True, True, True, False, False]
    assert not indexer.index(entries[0])  # tried this session
    assert indexer.index(entries[0], again=True)  # but an edit asks for it again
    indexer.close()


def test_missing_ocr_language_leaves_snips_unread(qapp, tmp_path, white_image):
    lib = Library(tmp_path)
    first, second = lib.create(white_image), lib.create(white_image)
    calls = []

    def unavailable(_image):
        calls.append(1)
        raise ocr.OcrUnavailable("No OCR language pack is installed in Windows.")

    indexer = TextIndexer(lib, unavailable)
    indexer.index(first)
    assert wait_until(lambda: not indexer.available and indexer.pending() == 0)
    assert not lib.meta(first).indexed  # read once the language pack is installed
    assert not indexer.index(second) and calls == [1]
    indexer.close()


def test_errors_are_retried_next_session_and_never_escape(qapp, tmp_path, white_image):
    lib = Library(tmp_path)
    entry = lib.create(white_image)

    def boom(_image):
        raise RuntimeError("WinRT hiccup")

    indexer = TextIndexer(lib, boom)
    indexer.index(entry)
    assert wait_until(lambda: indexer.pending() == 0)
    assert not lib.meta(entry).indexed
    assert not indexer.index(entry)  # not retried until the next start
    indexer.close()
    assert not indexer.index(entry, again=True)  # closed


def test_close_stops_an_in_flight_read_from_writing(qapp, tmp_path, white_image):
    lib = Library(tmp_path)
    entry = lib.create(white_image)
    started, release = threading.Event(), threading.Event()

    def slow(_image):
        started.set()
        release.wait(5)
        return "late words"

    indexer = TextIndexer(lib, slow)
    indexer.index(entry)
    assert started.wait(5)
    indexer.close()
    release.set()
    time.sleep(0.2)
    assert lib.meta(entry).text == "" and not lib.meta(entry).indexed


def test_turned_off_indexing_queues_nothing(qapp, tmp_path, white_image):
    lib = Library(tmp_path)
    indexer = TextIndexer(lib, lambda image: "x")
    indexer.enabled = False
    assert not indexer.index(lib.create(white_image))
    indexer.close()


def test_editing_a_snip_clears_its_old_words(tmp_path, white_image):
    lib = Library(tmp_path)
    entry = lib.create(white_image)
    lib.update_meta(entry, text="SSN 123-45-6789", indexed=True)
    lib.save(entry, {"items": []}, white_image)
    assert lib.meta(entry).text == "" and not lib.meta(entry).indexed
