"""Short synthetic clips, encoded with Qt Multimedia.

The packaged self-test makes one to prove recording's encoder made it into the build (Qt Multimedia,
its FFmpeg libraries and the system's H.264 encoder), and tests use them as material to work on:
each frame shows its own number as a row of blocks (frame_number() reads it back), so tests can tell
exactly which frame they're looking at.

Clips are written the way recordings will be: frames through QVideoFrameInput and sound through
QAudioBufferInput into QMediaRecorder, as MP4 with H.264 video and AAC sound. (Not Matroska: Qt 6.12
offers no H.264 in Matroska and silently encodes MPEG-4 Part 2 instead, which browsers can't play.)
"""
from __future__ import annotations

import array
import logging
import math
from pathlib import Path

from PySide6.QtCore import QByteArray, QEventLoop, QPoint, QRect, QSize, QTimer, QUrl
from PySide6.QtGui import QColor, QImage, QPainter

log = logging.getLogger(__name__)

SAMPLE_RATE = 48000
CHANNELS = 2
AUDIO_CHUNK = 960  # samples per buffer (20 ms)
TIMEOUT_MS = 20_000
STOP_TIMEOUT_MS = 5_000  # for a recording abandoned after an error or a timeout to let go of its file
NUMBER_BITS = 10  # each frame shows its number (up to 1023) as a row of black and white blocks


class ClipError(RuntimeError):
    """A clip couldn't be written."""


def audio_format():
    from PySide6.QtMultimedia import QAudioFormat

    fmt = QAudioFormat()
    fmt.setSampleRate(SAMPLE_RATE)
    fmt.setChannelCount(CHANNELS)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    return fmt


def _block(bit: int, width: int, height: int) -> QRect:
    """Where bit `bit` of the frame number is drawn: big enough to survive compression and scaling."""
    side = max(4, width // 16)
    centre = QPoint(round((bit + 1) * width / (NUMBER_BITS + 2)), round(height / 6))
    return QRect(centre.x() - side // 2, centre.y() - side // 2, side, side)


def frame_number(image: QImage) -> int:
    """The number a test clip's frame shows (works on scaled and re-encoded frames too)."""
    number = 0
    for bit in range(NUMBER_BITS):
        if image.pixelColor(_block(bit, image.width(), image.height()).center()).lightness() > 128:
            number |= 1 << bit
    return number


def _frame(index: int, size: tuple[int, int], fps: int):
    from PySide6.QtMultimedia import QVideoFrame

    image = QImage(size[0], size[1], QImage.Format.Format_ARGB32)
    image.fill(QColor.fromHsv(index * 7 % 360, 180, 120))
    painter = QPainter(image)
    for bit in range(NUMBER_BITS):
        painter.fillRect(_block(bit, *size), QColor("white") if index >> bit & 1 else QColor("black"))
    painter.end()
    frame = QVideoFrame(image)
    frame.setStartTime(round(index * 1_000_000 / fps))
    frame.setEndTime(round((index + 1) * 1_000_000 / fps))
    return frame


def _tone(first: int, count: int, hz: float, fmt):
    from PySide6.QtMultimedia import QAudioBuffer

    samples = array.array("f")
    for n in range(first, first + count):
        value = 0.25 * math.sin(2 * math.pi * hz * n / SAMPLE_RATE)
        samples.extend((value,) * CHANNELS)
    return QAudioBuffer(QByteArray(samples.tobytes()), fmt, round(first * 1_000_000 / SAMPLE_RATE))


def make_test_clip(path: Path, seconds: float = 1.0, size: tuple[int, int] = (320, 240), fps: int = 30,
                   tone_hz: float = 440.0, container: str = "mp4") -> Path:
    """Encode `seconds` of numbered frames and a tone to `path` (MP4: H.264 + AAC).

    The file is checked to start like an MP4, and recorded once more (with a warning in the log) if
    it doesn't. In early test runs, about 1 run in 65 had one clip come out as something else; since
    recorders always shut down and let go of their file, the stress test (pytest -m stress) made 800
    recordings without one bad file.
    """
    path = Path(path).resolve()  # Qt puts a relative name in the Videos folder
    _record(path, seconds, size, fps, tone_hz, container)
    if container == "mp4" and not _starts_like_mp4(path):
        log.warning("A test clip didn't come out as MP4 (it starts %r); recording it again", _head(path))
        path.unlink(missing_ok=True)
        _record(path, seconds, size, fps, tone_hz, container)
        if not _starts_like_mp4(path):
            raise ClipError(f"The test clip isn't an MP4 file (it starts {_head(path)!r})")
    return path


def _record(path: Path, seconds: float, size: tuple[int, int], fps: int, tone_hz: float,
            container: str = "mp4") -> None:
    _ClipRecording(path, seconds, size, fps, tone_hz, container).run()


def _head(path: Path) -> bytes:
    with open(path, "rb") as f:
        return f.read(16)


def _starts_like_mp4(path: Path) -> bool:
    return _head(path)[4:8] == b"ftyp"  # every MP4 opens with an ftyp box


class _ClipRecording:
    """One clip being recorded. run() always ends with every signal disconnected, the timers
    stopped and the recorder stopped, so nothing outlives it (Qt's connections to these methods
    would otherwise keep the recorder, and its open file, alive)."""

    def __init__(self, path: Path, seconds: float, size: tuple[int, int], fps: int, tone_hz: float,
                 container: str = "mp4") -> None:
        from PySide6 import QtMultimedia as M

        self.M = M
        self.path, self.size, self.fps, self.tone_hz = path, size, fps, tone_hz
        self.total_frames = max(1, round(seconds * fps))
        self.total_samples = round(seconds * SAMPLE_RATE)
        self.frames = self.samples = 0
        self.stopping = False
        self.ended_early = False
        self.errors: list[str] = []
        self.loop = QEventLoop()
        self.session = M.QMediaCaptureSession()
        video_format = M.QVideoFrameFormat(QSize(*size), M.QVideoFrameFormat.PixelFormat.Format_BGRA8888)
        video_format.setStreamFrameRate(fps)
        self.video = M.QVideoFrameInput(video_format)
        self.sound_format = audio_format()
        self.sound = M.QAudioBufferInput(self.sound_format)
        self.recorder = M.QMediaRecorder()
        from . import ffmpeg

        ffmpeg.quiet_logs()  # as the encoder does: FFmpeg's log lines could land in the clip
        self.session.setVideoFrameInput(self.video)
        self.session.setAudioBufferInput(self.sound)
        self.session.setRecorder(self.recorder)
        # "mkv" makes Qt's Matroska, with its quirks, to test reading those (MPEG-4 Part 2, not H.264)
        media = M.QMediaFormat({"mp4": M.QMediaFormat.FileFormat.MPEG4,
                                "mkv": M.QMediaFormat.FileFormat.Matroska}[container])
        media.setVideoCodec(M.QMediaFormat.VideoCodec.H264)
        media.setAudioCodec(M.QMediaFormat.AudioCodec.AAC)
        self.recorder.setMediaFormat(media)
        self.recorder.setVideoResolution(*size)
        self.recorder.setVideoFrameRate(fps)
        self.recorder.setOutputLocation(QUrl.fromLocalFile(str(path)))
        self.pump_timer = QTimer()
        self.deadline = QTimer()
        self.deadline.setSingleShot(True)
        self.connections = [(self.recorder.errorOccurred, self._on_error),
                            (self.recorder.recorderStateChanged, self._on_state),
                            (self.video.readyToSendVideoFrame, self._pump),
                            (self.sound.readyToSendAudioBuffer, self._pump),
                            (self.pump_timer.timeout, self._pump),
                            (self.deadline.timeout, self.loop.quit)]
        for signal, slot in self.connections:
            signal.connect(slot)

    def run(self) -> None:
        try:
            self.pump_timer.start(5)
            self.deadline.start(TIMEOUT_MS)
            self.recorder.record()
            if self.recorder.error() != self.M.QMediaRecorder.Error.NoError:
                self.errors.append(self.recorder.errorString())
            if not self.errors:
                self.loop.exec()
        finally:
            self._close()
        self._check()

    def _pump(self) -> None:
        while self.frames < self.total_frames and self.video.sendVideoFrame(_frame(self.frames, self.size, self.fps)):
            self.frames += 1
        while self.samples < self.total_samples:
            count = min(AUDIO_CHUNK, self.total_samples - self.samples)
            if not self.sound.sendAudioBuffer(_tone(self.samples, count, self.tone_hz, self.sound_format)):
                break
            self.samples += count
        finished = self.frames == self.total_frames and self.samples == self.total_samples
        if finished and self._recording() and not self.stopping:
            self.stopping = True
            self.recorder.stop()

    def _recording(self) -> bool:
        return self.recorder.recorderState() == self.M.QMediaRecorder.RecorderState.RecordingState

    def _on_error(self, _error, message: str) -> None:
        self.errors.append(message or "the recorder failed")
        self.loop.quit()

    def _on_state(self, state) -> None:
        if state == self.M.QMediaRecorder.RecorderState.StoppedState:
            self.loop.quit()

    def _close(self) -> None:
        self.pump_timer.stop()
        self.deadline.stop()
        for signal, slot in self.connections:
            signal.disconnect(slot)
        self.connections.clear()
        stopped = self.M.QMediaRecorder.RecorderState.StoppedState
        if self.recorder.recorderState() != stopped:  # gave up on it: stop, so it lets go of the file
            self.ended_early = True
            waiting, limit = QEventLoop(), QTimer()
            limit.setSingleShot(True)
            limit.timeout.connect(waiting.quit)
            self.recorder.recorderStateChanged.connect(waiting.quit)
            self.recorder.stop()
            if self.recorder.recorderState() != stopped:
                limit.start(STOP_TIMEOUT_MS)
                waiting.exec()
            limit.stop()
            limit.timeout.disconnect(waiting.quit)
            self.recorder.recorderStateChanged.disconnect(waiting.quit)

    def _check(self) -> None:
        if self.errors:
            raise ClipError(f"Couldn't record a test clip: {self.errors[0]}")
        if self.ended_early or not self.stopping:
            raise ClipError(f"Recording a test clip timed out ({self.frames} frames, {self.samples} samples sent)")
        if not self.path.is_file() or self.path.stat().st_size == 0:
            raise ClipError(f"The test clip wasn't written to {self.path}")
