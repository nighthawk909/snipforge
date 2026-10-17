"""Short synthetic clips, encoded with Qt Multimedia.

The packaged self-test makes one to prove recording's encoder made it into the build (Qt Multimedia,
its FFmpeg libraries and Windows' H.264 encoder), and tests use them as material to work on.

Clips are written the way recordings will be: frames through QVideoFrameInput and sound through
QAudioBufferInput into QMediaRecorder, as MP4 with H.264 video and AAC sound. (Not Matroska: Qt 6.12
offers no H.264 in Matroska and silently encodes MPEG-4 Part 2 instead, which browsers can't play.)
"""
from __future__ import annotations

import array
import logging
import math
from pathlib import Path

from PySide6.QtCore import QByteArray, QEventLoop, QSize, QTimer, QUrl
from PySide6.QtGui import QColor, QImage

log = logging.getLogger(__name__)

SAMPLE_RATE = 48000
CHANNELS = 2
AUDIO_CHUNK = 960  # samples per buffer (20 ms)
TIMEOUT_MS = 20_000


class ClipError(RuntimeError):
    """A clip couldn't be written."""


def audio_format():
    from PySide6.QtMultimedia import QAudioFormat

    fmt = QAudioFormat()
    fmt.setSampleRate(SAMPLE_RATE)
    fmt.setChannelCount(CHANNELS)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    return fmt


def _frame(index: int, size: tuple[int, int], fps: int):
    from PySide6.QtMultimedia import QVideoFrame

    image = QImage(size[0], size[1], QImage.Format.Format_ARGB32)
    image.fill(QColor.fromHsv(index * 7 % 360, 180, 220))
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
                   tone_hz: float = 440.0) -> Path:
    """Encode `seconds` of changing colour and a tone to `path` (MP4: H.264 + AAC).

    The file is checked to start like an MP4. In about 1 test run in 65, one clip came out as
    something else (FFmpeg read it as a different format), which never happened in 140 clips made
    outside the test runner; that clip is recorded once more, with a warning in the log. The
    recorder step investigates this with a stress test (docs/VIDEO-PLAN.md, Risks)."""
    path = Path(path)
    _record(path, seconds, size, fps, tone_hz)
    if not _starts_like_mp4(path):
        log.warning("A test clip didn't come out as MP4 (it starts %r); recording it again", _head(path))
        path.unlink(missing_ok=True)
        _record(path, seconds, size, fps, tone_hz)
        if not _starts_like_mp4(path):
            raise ClipError(f"The test clip isn't an MP4 file (it starts {_head(path)!r})")
    return path


def _head(path: Path) -> bytes:
    with open(path, "rb") as f:
        return f.read(16)


def _starts_like_mp4(path: Path) -> bool:
    return _head(path)[4:8] == b"ftyp"  # every MP4 opens with an ftyp box


def _record(path: Path, seconds: float, size: tuple[int, int], fps: int, tone_hz: float) -> None:
    from PySide6 import QtMultimedia as M

    session = M.QMediaCaptureSession()
    video_format = M.QVideoFrameFormat(QSize(*size), M.QVideoFrameFormat.PixelFormat.Format_BGRA8888)
    video_format.setStreamFrameRate(fps)
    video = M.QVideoFrameInput(video_format)
    sound_format = audio_format()
    sound = M.QAudioBufferInput(sound_format)
    recorder = M.QMediaRecorder()
    session.setVideoFrameInput(video)
    session.setAudioBufferInput(sound)
    session.setRecorder(recorder)
    media = M.QMediaFormat(M.QMediaFormat.FileFormat.MPEG4)
    media.setVideoCodec(M.QMediaFormat.VideoCodec.H264)
    media.setAudioCodec(M.QMediaFormat.AudioCodec.AAC)
    recorder.setMediaFormat(media)
    recorder.setVideoResolution(*size)
    recorder.setVideoFrameRate(fps)
    recorder.setOutputLocation(QUrl.fromLocalFile(str(path)))

    total_frames = max(1, round(seconds * fps))
    total_samples = round(seconds * SAMPLE_RATE)
    sent = {"frames": 0, "samples": 0, "stopping": False}
    errors: list[str] = []
    loop = QEventLoop()

    def pump() -> None:
        while sent["frames"] < total_frames and video.sendVideoFrame(_frame(sent["frames"], size, fps)):
            sent["frames"] += 1
        while sent["samples"] < total_samples:
            count = min(AUDIO_CHUNK, total_samples - sent["samples"])
            if not sound.sendAudioBuffer(_tone(sent["samples"], count, tone_hz, sound_format)):
                break
            sent["samples"] += count
        finished = sent["frames"] == total_frames and sent["samples"] == total_samples
        recording = recorder.recorderState() == M.QMediaRecorder.RecorderState.RecordingState
        if finished and recording and not sent["stopping"]:
            sent["stopping"] = True
            recorder.stop()

    def on_error(_error, message: str) -> None:
        errors.append(message or "the recorder failed")
        loop.quit()

    def on_state(state) -> None:
        if state == M.QMediaRecorder.RecorderState.StoppedState:
            loop.quit()

    recorder.errorOccurred.connect(on_error)
    recorder.recorderStateChanged.connect(on_state)
    video.readyToSendVideoFrame.connect(pump)
    sound.readyToSendAudioBuffer.connect(pump)
    timer = QTimer()
    timer.timeout.connect(pump)
    timer.start(5)
    QTimer.singleShot(TIMEOUT_MS, loop.quit)
    recorder.record()
    if recorder.error() != M.QMediaRecorder.Error.NoError:
        errors.append(recorder.errorString())
    if not errors:
        loop.exec()
    timer.stop()
    if errors:
        raise ClipError(f"Couldn't record a test clip: {errors[0]}")
    if not sent["stopping"] or recorder.recorderState() != M.QMediaRecorder.RecorderState.StoppedState:
        raise ClipError(f"Recording a test clip timed out ({sent['frames']} frames, {sent['samples']} samples sent)")
    if not path.is_file() or path.stat().st_size == 0:
        raise ClipError(f"The test clip wasn't written to {path}")
