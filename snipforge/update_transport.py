"""Fetching bytes for updates: https through Qt's network stack, or files from a folder feed.

Both transports call `done(data | None, error, final_location)` on the main thread and return a
transfer whose cancel() makes `done` report CANCELED. Sizes are capped so a bad server or share
can't fill memory.
"""
from __future__ import annotations

import os
import threading
from collections.abc import Callable
from urllib.parse import urlparse

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import __version__

CANCELED = "Canceled"
# HTTP refusals worth telling apart: sources turn the first two into advice (see update_sources.py).
NEEDS_SIGN_IN = "The server wants a sign-in before it shares this, and SnipForge doesn't sign in."
NOT_FOUND = "The server has nothing at that address."
RATE_LIMITED = "The server is limiting requests right now. Try again later."
CHUNK = 1 << 20

Done = Callable[[bytes | None, str, str], None]
Progress = Callable[[int, int], None]


class _ReplyTransfer:
    def __init__(self, reply) -> None:
        self.reply = reply
        self.cancelled = False
        self.too_big = False

    def cancel(self) -> None:
        self.cancelled = True
        self.reply.abort()


class NetworkTransport(QObject):
    """https GETs with a timeout, a size cap and no downgrade to http on redirect."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._network: QNetworkAccessManager | None = None

    def get(self, url: str, *, timeout_ms: int, max_bytes: int, done: Done, progress: Progress | None = None,
            accept: str | None = None):
        if urlparse(url or "").scheme != "https":
            QTimer.singleShot(0, lambda: done(None, "Only https:// downloads are allowed.", url))
            return _NoTransfer()
        if self._network is None:
            self._network = QNetworkAccessManager(self)
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", f"SnipForge/{__version__}".encode())
        if accept:
            request.setRawHeader(b"Accept", accept.encode())
        request.setTransferTimeout(timeout_ms)
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.NoLessSafeRedirectPolicy)
        reply = self._network.get(request)
        transfer = _ReplyTransfer(reply)

        def on_progress(received: int, total: int) -> None:
            if received > max_bytes or total > max_bytes:
                transfer.too_big = True
                reply.abort()
            elif progress is not None:
                progress(received, total)

        reply.downloadProgress.connect(on_progress)
        reply.finished.connect(lambda: self._finished(reply, transfer, max_bytes, done))
        return transfer

    def _finished(self, reply, transfer: _ReplyTransfer, max_bytes: int, done: Done) -> None:
        reply.deleteLater()
        final = reply.url().toString()
        error = reply.error()
        if transfer.too_big:
            done(None, "The download was larger than expected, so it was stopped.", final)
        elif error == QNetworkReply.OperationCanceledError:
            done(None, CANCELED if transfer.cancelled else "The connection timed out.", final)
        elif error != QNetworkReply.NoError:
            status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
            done(None, _refusal(reply, status) or reply.errorString(), final)
        else:
            data = bytes(reply.readAll())
            if len(data) > max_bytes:
                done(None, "The download was larger than expected, so it was stopped.", final)
            else:
                done(data, "", final)


def _refusal(reply, status) -> str | None:
    """GitHub answers 403 both when it's rate limiting (with these headers) and when it refuses access."""
    limited = reply.hasRawHeader("Retry-After") or bytes(reply.rawHeader("X-RateLimit-Remaining")).strip() == b"0"
    if status == 429 or (status == 403 and limited):
        return RATE_LIMITED
    if status in (401, 403):
        return NEEDS_SIGN_IN
    if status == 404:
        return NOT_FOUND
    return None


class _NoTransfer:
    def cancel(self) -> None:
        pass


class _FileTransfer:
    def __init__(self, finish: Callable[[bytes | None, str], None]) -> None:
        self.cancelled = False
        self.finished = False
        self._finish = finish

    def cancel(self) -> None:
        self.cancelled = True
        self._finish(None, CANCELED)


class _TooBig(Exception):
    pass


class FileTransport(QObject):
    """Reads a file (local, UNC share or synced SharePoint library) on a worker thread.

    Worker threads are daemons, so a share that stops answering can't keep SnipForge from quitting.
    The timeout counts from the last progress, like the network's transfer timeout, and reports the
    failure even if a read never returns.
    """

    _deliver = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._deliver.connect(self._run, Qt.QueuedConnection)

    @Slot(object)
    def _run(self, callback) -> None:
        callback()

    def _post(self, callback) -> None:
        try:
            self._deliver.emit(callback)
        except RuntimeError:
            pass  # the transport was deleted while the worker was reading; nobody is waiting

    def get(self, path: str, *, timeout_ms: int, max_bytes: int, done: Done, progress: Progress | None = None,
            accept: str | None = None):
        timer = QTimer(self)
        timer.setSingleShot(True)

        def finish(data: bytes | None, error: str) -> None:  # main thread
            if transfer.finished:
                return
            transfer.finished = True
            timer.stop()
            timer.deleteLater()
            done(data, error, path)

        def timed_out() -> None:
            transfer.cancelled = True  # stops the worker at its next chunk
            finish(None, f"Reading {path} timed out.")

        def on_progress(received: int, total: int) -> None:  # main thread
            if transfer.finished:
                return
            timer.start(timeout_ms)  # still answering
            if progress is not None:
                progress(received, total)

        transfer = _FileTransfer(finish)
        timer.timeout.connect(timed_out)
        timer.start(timeout_ms)

        def report(received: int, total: int) -> None:
            self._post(lambda: on_progress(received, total))

        def work() -> None:  # worker thread
            try:
                data = _read(path, max_bytes, transfer, report)
            except _TooBig:
                self._post(lambda: finish(None, "The file was larger than expected, so it wasn't read."))
            except OSError as exc:
                message = f"Couldn't read {path}: {exc.strerror or exc}"
                self._post(lambda: finish(None, message))
            else:
                self._post(lambda: finish(data, ""))

        threading.Thread(target=work, name="snipforge-update-read", daemon=True).start()
        return transfer


def _read(path: str, max_bytes: int, transfer: _FileTransfer, report: Progress) -> bytearray | None:
    total = os.path.getsize(path)
    if total > max_bytes:
        raise _TooBig
    data = bytearray()
    with open(path, "rb") as handle:
        while chunk := handle.read(CHUNK):
            if transfer.cancelled:
                return None
            data += chunk
            if len(data) > max_bytes:
                raise _TooBig
            report(len(data), total)
    return data
