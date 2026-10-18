"""Playing a video in the editor: Qt's QMediaPlayer, with sound through QAudioOutput and pictures
through a QVideoSink that the editor draws itself (no QtMultimediaWidgets, which the build leaves
out).

Qt 6.12's player now and then loads a fresh file with no tracks, or never shows a first frame
(docs/VIDEO-PLAN.md, Risks). So every start is watched: if no picture arrives in a few seconds, or
the file loads without video, or the player reports an error, the file is loaded again and playing
resumes where it was, a couple of times before giving up with `failed`. Trimming and export don't
need the player, so a video that won't play can still be cut.

Qt's player also drops a seek made before the file has loaded and plays from the start, so playing
waits for the file to load before seeking.

Qt Multimedia is imported, and the player made, only on the first play. Qt's player is limited to
local files (QT_FFMPEG_PROTOCOL_WHITELIST=file, set when snipforge.video is imported), as
SnipForge's own FFmpeg calls are.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QImage

log = logging.getLogger(__name__)

FIRST_FRAME_TIMEOUT_MS = 5000  # a first play opens the sound device too, which took up to 3 s in tests
RETRIES = 2


def _qt_backend():
    """A real QMediaPlayer with sound and a video sink; returns (player, sink)."""
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink

    player = QMediaPlayer()
    sink = QVideoSink(player)
    player.setAudioOutput(QAudioOutput(player))
    player.setVideoSink(sink)
    return player, sink


class Player(QObject):
    frame_shown = Signal(QImage, float)  # the picture, and when in the video it belongs (seconds)
    finished = Signal()  # played to the end of the video
    failed = Signal(str)  # gave up; says why, for people

    def __init__(self, parent: QObject | None = None, *, make_backend: Callable[[], tuple] | None = None,
                 first_frame_timeout_ms: int = FIRST_FRAME_TIMEOUT_MS, retries: int = RETRIES) -> None:
        super().__init__(parent)
        self._make_backend = make_backend or _qt_backend
        self._media = self._sink = None
        self._url = QUrl()
        self._retries = retries
        self._tries = 0
        self._playing = False
        self._waiting_for_load = False
        self._resume_at = 0.0
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.setInterval(first_frame_timeout_ms)
        self._watchdog.timeout.connect(self._stalled)

    @property
    def is_playing(self) -> bool:
        return self._playing

    def load(self, path: str | os.PathLike) -> None:
        if "://" in os.fspath(path):
            raise ValueError("Only local files can be played")
        self._url = QUrl.fromLocalFile(str(Path(path).resolve()))
        if self._media is not None:
            self._media.setSource(self._url)

    def play(self, at: float) -> None:
        """Play from `at` seconds; pictures follow through frame_shown."""
        if self._media is None:
            self._open()
        self._playing, self._tries, self._resume_at = True, 0, max(0.0, at)
        self._start()

    def pause(self) -> None:
        self._playing = False
        self._watchdog.stop()
        if self._media is not None:
            self._media.pause()

    def close(self) -> None:
        self._playing = False
        self._watchdog.stop()
        if self._media is None:
            return
        self._media.stop()
        self._sink.videoFrameChanged.disconnect(self._on_frame)
        self._media.mediaStatusChanged.disconnect(self._on_status)
        self._media.errorOccurred.disconnect(self._on_error)
        if self._media is not self._sink and isinstance(self._media, QObject):
            self._media.deleteLater()  # its sink and sound output are its children
        self._media = self._sink = None

    def _open(self) -> None:
        self._media, self._sink = self._make_backend()
        self._sink.videoFrameChanged.connect(self._on_frame)
        self._media.mediaStatusChanged.connect(self._on_status)
        self._media.errorOccurred.connect(self._on_error)
        self._media.setSource(self._url)

    def _start(self) -> None:
        """Seek and play now, or once the file has loaded (Qt drops a seek made before that)."""
        from PySide6.QtMultimedia import QMediaPlayer

        self._watchdog.start()
        loading = (QMediaPlayer.MediaStatus.NoMedia, QMediaPlayer.MediaStatus.LoadingMedia)
        self._waiting_for_load = self._media.mediaStatus() in loading
        if not self._waiting_for_load:
            self._seek_and_play()

    def _seek_and_play(self) -> None:
        self._media.setPosition(round(self._resume_at * 1000))
        self._media.play()

    def _on_frame(self, frame) -> None:
        if not self._playing or not frame.isValid():
            return  # a late frame after a pause, or the empty one Qt sends at the end
        self._watchdog.stop()
        image = frame.toImage()
        if image.isNull():
            return
        seconds = max(0, frame.startTime()) / 1_000_000
        self._resume_at = seconds
        self.frame_shown.emit(image, seconds)

    def _on_status(self, status) -> None:
        from PySide6.QtMultimedia import QMediaPlayer

        if not self._playing:
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._playing = False
            self._watchdog.stop()
            self.finished.emit()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._reload("Qt's player couldn't read the video")
        elif status == QMediaPlayer.MediaStatus.LoadedMedia and not self._media.hasVideo():
            self._reload("Qt's player loaded the video without its picture")
        elif status == QMediaPlayer.MediaStatus.LoadedMedia and self._waiting_for_load:
            self._waiting_for_load = False
            self._seek_and_play()

    def _on_error(self, _error, message: str) -> None:
        if self._playing:
            self._reload(message or "Qt's player reported an error")

    def _stalled(self) -> None:
        if self._playing:
            self._reload(f"no picture after {self._watchdog.interval() / 1000:g} s")

    def _reload(self, reason: str) -> None:
        """Load the file again and carry on from the last picture shown, or give up."""
        self._watchdog.stop()
        if self._tries >= self._retries:
            log.warning("Gave up playing %s: %s", self._url.toLocalFile(), reason)
            self.pause()
            self.failed.emit("The video didn't start playing here. Trimming and GIF export still work.")
            return
        self._tries += 1
        log.warning("Loading %s again (%s), try %d", self._url.toLocalFile(), reason, self._tries)
        self._media.stop()
        self._media.setSource(QUrl())
        self._media.setSource(self._url)
        self._start()
