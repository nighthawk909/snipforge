"""Saving an edited video: a title card, parts cut out of the middle, or a caption mean every frame
is drawn and encoded again (a plain trim is copied as it is, by trim.py). The plans are edit.py's.

- edited_frames: the saved video's frames, (seconds, image), read with frames.read and drawn on;
  for the MP4 and for a GIF of the edit.
- Render: the MP4. Frames are read and drawn on a thread of their own and handed to the recorder's
  encoder (encoder.py, which wants the Qt main thread) a few at a time, so memory stays small and the
  window stays usable. The sound is decoded by Qt's QAudioDecoder, already in the encoder's format
  (48 kHz stereo float), a buffer at a time and never far ahead of the video (the decoder waits
  until its buffer is read); the parts of it that are kept go in one unbroken stream, with silence
  under the title card. The file is written next to its final name and renamed when it's complete, so a
  failed or cancelled save leaves nothing behind.
"""
from __future__ import annotations

import array
import contextlib
import logging
import os
import queue
import statistics
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

from PySide6.QtCore import QObject, QSize, QTimer, QUrl, Signal
from PySide6.QtGui import QImage

from . import ffmpeg, frames, gif
from .edit import VideoEdit, output_plan, sound_plan, title_card, with_caption
from .encoder import CHANNELS, SAMPLE_RATE, Encoder, EncoderError, fit
from .tasks import Task
from .timeline import FrameTimes

log = logging.getLogger(__name__)

FRAME_QUEUE = 6  # frames drawn ahead of the encoder (each a full-size picture)
ENCODER_BACKLOG = 3  # frames and sound buffers the encoder may hold before it gets more
PUMP_MS = 4
MAX_FPS = 60
SOUND_LEAD = 1.0  # seconds the sound may get ahead of the video handed to the encoder
_DONE = object()


def frame_rate(times: FrameTimes) -> int:
    """The recording's frame rate, from its usual gap between frames (busy moments leave out frames,
    so the average would be too low)."""
    gaps = [b - a for a, b in zip(times.starts, times.starts[1:], strict=False)] or [times.end - times.starts[0]]
    return max(1, min(MAX_FPS, round(1 / statistics.median(gaps))))


def edited_frames(path: str | os.PathLike, edit: VideoEdit, fps: float | None = None, width: int | None = None,
                  cancel: Callable[[], bool] | None = None) -> Iterator[tuple[float, QImage]]:
    """(seconds into the saved video, picture) for its frames: the title card, then the kept frames
    with each cut closed up and the caption drawn on. With `fps`, at most that many a second; with
    `width`, shrunk to it (as frames.read does)."""
    spans = edit.kept_spans()
    readers = []
    for first, stop in spans:
        start = edit.frames.boundary(first)
        end = None if stop == len(edit.frames) else edit.frames.boundary(stop)  # the sound after the last frame too
        readers.append((start, edit.span_seconds((first, stop)),
                        lambda s=start, e=end: frames.read(path, fps=fps, width=width, start=s, end=e)))
    offset = edit.title.seconds if edit.title else 0.0
    title_due = edit.title is not None
    for start, length, read in readers:
        with contextlib.closing(read()) as images:
            for t, image in images:
                if cancel is not None and cancel():
                    return
                if title_due:  # before the first frame, now its size is known
                    yield from _title_frames(edit, image.size(), fps)
                    title_due = False
                yield offset + t - start, with_caption(image, edit.caption)
        offset += length


def _title_frames(edit: VideoEdit, size: QSize, fps: float | None) -> Iterator[tuple[float, QImage]]:
    card = title_card(edit.title, size)
    step = 1 / (fps or frame_rate(edit.frames))
    for n in range(max(1, round(edit.title.seconds / step))):
        yield n * step, card


def export_edited_gif(src: str | os.PathLike, dst: str | os.PathLike, edit: VideoEdit, fps: float = 12,
                      width: int = 960, progress: Callable[[float], None] | None = None,
                      cancel: Callable[[], bool] | None = None) -> Path:
    """The edit as a GIF, written whole or not at all (gif.export_gif's rules)."""
    src, dst = _check_names(src, dst)
    handle, partial_name = tempfile.mkstemp(dir=dst.parent, prefix=f".{dst.stem}.", suffix=".partial.gif")
    partial = Path(partial_name)
    try:
        with os.fdopen(handle, "wb") as out, \
                contextlib.closing(edited_frames(src, edit, fps=fps, width=width, cancel=cancel)) as images:
            gif.write_gif(out, images, 0.0, edit.seconds, fps, progress, cancel)
        os.replace(partial, dst)
    finally:
        _remove(partial)
    return dst


def _check_names(src, dst) -> tuple[Path, Path]:
    if any("://" in os.fspath(p) for p in (src, dst)):
        raise ValueError("Only local files can be saved")
    src, dst = Path(src), Path(dst)
    if ":" in dst.name:  # an NTFS alternate data stream
        raise ValueError(f"{dst.name} isn't a plain file name")
    if dst.exists() and src.exists() and os.path.samefile(src, dst):
        raise ValueError("The edited video can't replace the one it comes from")
    return src, dst


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:  # an antivirus or sync client holding it; never hide the real error
        log.warning("Couldn't remove %s: %s", path, exc)


class Render(QObject):
    """Writes the edit of `src` to `dst` (MP4: H.264 and AAC). Use from the Qt main thread: start(),
    then `finished(dst)`, `failed(message)` or, after cancel(), `cancelled`."""

    progress = Signal(float)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, src: str | os.PathLike, dst: str | os.PathLike, edit: VideoEdit,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.src, self.dst = _check_names(src, dst)
        self.edit = edit
        self.fps = frame_rate(edit.frames)
        self._total = max(1, len(output_plan(edit, self.fps)))
        self._sound_plan = sound_plan(edit)
        self._queue: queue.Queue = queue.Queue(maxsize=FRAME_QUEUE)
        self._sent = 0
        self._last_index = -1
        self._frames_done = False
        self._sound_done = True
        self._samples_out = 0  # sound handed to the encoder so far
        self._over = False
        self._encoder: Encoder | None = None
        self._decoder = None
        self._worker: Task | None = None
        self._partial: Path | None = None
        self._pump = QTimer(self, interval=PUMP_MS)
        self._pump.timeout.connect(self._feed)

    # ------------------------------------------------------------------ control
    def start(self) -> None:
        try:
            info = ffmpeg.probe(self.src)
            video, audio = info.first("video"), info.first("audio")
            if video is None:
                raise ffmpeg.FFmpegError(f"{self.src.name} has no video")
            size = (video.width, video.height)
            handle, name = tempfile.mkstemp(dir=self.dst.parent, prefix=f".{self.dst.stem}.", suffix=".partial.mp4")
            os.close(handle)
            self._partial = Path(name)
            self._encoder = Encoder(self._partial, size, fit(size, None), self.fps, audio=audio is not None,
                                    max_pending_frames=FRAME_QUEUE * 4)
            self._encoder.failed.connect(self._fail)
            self._encoder.finished.connect(self._done)
            self._encoder.start()
            if audio is not None:
                self._start_sound()
        except (OSError, ValueError, EncoderError, ffmpeg.FFmpegError) as exc:
            self._fail(str(exc))
            return
        self._worker = Task(self._draw, failed=self._fail, name="snipforge-video-render").start()
        self._pump.start()

    def cancel(self) -> None:
        if self._over:
            return
        self._finish("cancelled")
        self.cancelled.emit()

    # ------------------------------------------------------------------ frames
    def _draw(self, task: Task) -> None:
        """On the worker thread: every frame of the saved video, drawn, into the queue."""
        for item in edited_frames(self.src, self.edit, cancel=lambda: task.is_cancelled):
            self._put(task, item)
        self._put(task, _DONE)

    def _put(self, task: Task, item) -> None:
        while not task.is_cancelled:
            try:
                self._queue.put(item, timeout=0.1)
                return
            except queue.Full:
                continue

    def _feed(self) -> None:
        """On the main thread: frames to the encoder while it keeps up; finish once all is in."""
        encoder = self._encoder
        while encoder is not None and not self._frames_done and encoder.waiting < ENCODER_BACKLOG:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is _DONE:
                self._frames_done = True
                break
            at, image = item
            index = max(self._last_index + 1, round(at * self.fps))  # always forwards, even on a busy frame
            encoder.add_frame(index, image)
            self._last_index = index
            self._sent += 1
            self.progress.emit(min(0.99, self._sent / self._total))
        self._feed_sound()
        if self._frames_done and self._sound_done and encoder is not None and not self._over:
            self._pump.stop()
            encoder.finish()

    # ------------------------------------------------------------------ sound
    def _start_sound(self) -> None:
        from PySide6 import QtMultimedia as M  # loaded only when saving

        fmt = M.QAudioFormat()
        fmt.setSampleRate(SAMPLE_RATE)
        fmt.setChannelCount(CHANNELS)
        fmt.setSampleFormat(M.QAudioFormat.SampleFormat.Float)
        decoder = M.QAudioDecoder(self)
        decoder.setAudioFormat(fmt)
        decoder.setSource(QUrl.fromLocalFile(str(self.src)))
        decoder.finished.connect(self._sound_finished)
        decoder.error.connect(lambda _e: self._fail(f"The sound couldn't be read: {decoder.errorString()}"))
        self._decoder, self._sound_done = decoder, False
        decoder.start()

    def _feed_sound(self) -> None:
        """Read the decoder's buffers while the sound isn't ahead of the video (or the video is all in)."""
        decoder = self._decoder
        while (decoder is not None and not self._sound_done and not self._over and decoder.bufferAvailable()
               and (self._frames_done or self._samples_out / SAMPLE_RATE < self._last_index / self.fps + SOUND_LEAD)):
            buffer = decoder.read()
            samples = array.array("f", bytes(buffer.constData()))
            self.add_sound(buffer.startTime() / 1_000_000, samples)

    def add_sound(self, begin: float, samples: array.array) -> None:
        """Interleaved stereo from `begin` seconds into the recording: the kept parts, placed where
        they go in the saved video, after silence for anything skipped (the title card)."""
        count = len(samples) // CHANNELS
        end = begin + count / SAMPLE_RATE
        for at, start, stop in self._sound_plan:
            a, b = max(begin, start), min(end, stop)
            if a >= b:
                continue
            first = round((a - begin) * SAMPLE_RATE)
            last = min(count, round((b - begin) * SAMPLE_RATE))
            out_first = round((at + a - start) * SAMPLE_RATE)
            piece = samples[first * CHANNELS:last * CHANNELS]
            if out_first < self._samples_out:  # overlaps what's already in: skip that much
                piece = piece[(self._samples_out - out_first) * CHANNELS:]
                out_first = self._samples_out
            if out_first > self._samples_out:  # one unbroken stream: silence over the gap
                gap = out_first - self._samples_out
                self._encoder.add_audio(self._samples_out, array.array("f", bytes(4 * gap * CHANNELS)))
                self._samples_out = out_first
            if piece:
                self._encoder.add_audio(self._samples_out, piece)
                self._samples_out += len(piece) // CHANNELS

    def _sound_finished(self) -> None:
        while self._decoder is not None and self._decoder.bufferAvailable() and not self._over:  # the last ones
            buffer = self._decoder.read()
            self.add_sound(buffer.startTime() / 1_000_000, array.array("f", bytes(buffer.constData())))
        self._sound_done = True

    # ------------------------------------------------------------------ ending
    def _done(self) -> None:
        if self._over:
            return
        try:
            os.replace(self._partial, self.dst)
        except OSError as exc:
            self._fail(f"Couldn't save {self.dst.name}: {exc}")
            return
        self._finish("finished")
        self.progress.emit(1.0)
        self.finished.emit(self.dst)

    def _fail(self, message: str) -> None:
        if self._over:
            return
        self._finish("failed")
        self.failed.emit(message)

    def _finish(self, how: str) -> None:
        self._over = True
        self._pump.stop()
        if self._worker is not None:
            self._worker.cancel()
        if self._decoder is not None:
            self._decoder.stop()
        if self._encoder is not None:
            self._encoder.close(discard=how != "finished")  # discarding deletes the unfinished file
        elif self._partial is not None:
            _remove(self._partial)

    def wait(self, timeout: float) -> bool:
        """Wait for the drawing thread to end (after cancel or a finish), for closing windows."""
        return self._worker is None or self._worker.wait(timeout)
