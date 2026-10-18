"""Qt Multimedia's recorder, fed with frames and sound: H.264 video and AAC sound in MP4.

Frames go in through QVideoFrameInput with their own timestamps (Qt honours them, measured from the
first frame, so frame 0 must be at 0.0 s) and sound through QAudioBufferInput (Qt ignores buffer
timestamps and plays buffers end to end, so the sound must be one unbroken stream from 0.0 s, which
video/mixer.py makes). When the encoder falls behind, the oldest waiting frames are dropped rather
than queued: the video skips a frame but stays in time, and memory stays bounded.

Qt opens the video encoder after record() returns, on a thread of its own: ~0.1 s, but over a
second on a busy machine, and until then it takes no frames. `ready` says when it has, so a
recording can start its clock then instead of losing what it made in the meantime.

Big areas are scaled down by the encoder itself (output_size), in the same pass as its colour
conversion. Use from the Qt main thread.
"""
from __future__ import annotations

import array
from collections import deque
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, QSize, QUrl, Signal
from PySide6.QtGui import QImage

SAMPLE_RATE = 48000
CHANNELS = 2
AUDIO_BITRATE = 160_000
MAX_PENDING_FRAMES = 6  # waiting for the encoder; beyond this the oldest is dropped


class EncoderError(RuntimeError):
    """The recorder couldn't start, or failed while recording."""


def fit(size: tuple[int, int], limit: tuple[int, int] | None) -> tuple[int, int]:
    """`size` shrunk (never grown) to fit inside `limit`, keeping its shape; even, as H.264 needs."""
    width, height = size
    if limit:
        scale = min(1.0, limit[0] / width, limit[1] / height)
        width, height = round(width * scale), round(height * scale)
    return max(2, width // 2 * 2), max(2, height // 2 * 2)


class Encoder(QObject):
    """Writes `path` (MP4) from frames of `frame_size` at `fps`, scaled to `output_size`."""

    ready = Signal()  # it takes frames (and sound) now
    finished = Signal()  # the file is complete
    failed = Signal(str)

    def __init__(self, path: Path, frame_size: tuple[int, int], output_size: tuple[int, int], fps: int,
                 audio: bool = True, max_pending_frames: int = MAX_PENDING_FRAMES) -> None:
        super().__init__()
        from PySide6 import QtMultimedia as M  # loaded only when recording (startup stays as fast)

        self.M = M
        self.path, self.fps, self.has_audio = Path(path).resolve(), fps, audio
        self.max_pending = max_pending_frames
        self.frames_sent = self.frames_dropped = self.buffers_sent = 0
        self.error = ""
        self._pending_video: deque = deque()
        self._pending_audio: deque = deque()
        self._finishing = False
        self._first_sent = False  # frame 0 must reach the encoder: Qt times everything from it
        self._ready_inputs: set[str] = set()
        self._inputs = {"video", "audio"} if audio else {"video"}
        self.session = M.QMediaCaptureSession()
        video_format = M.QVideoFrameFormat(QSize(*frame_size), M.QVideoFrameFormat.PixelFormat.Format_BGRX8888)
        video_format.setStreamFrameRate(fps)
        self.video = M.QVideoFrameInput(video_format)
        self.session.setVideoFrameInput(self.video)
        self.sound = self.audio_format = None
        if audio:
            self.audio_format = M.QAudioFormat()
            self.audio_format.setSampleRate(SAMPLE_RATE)
            self.audio_format.setChannelCount(CHANNELS)
            self.audio_format.setSampleFormat(M.QAudioFormat.SampleFormat.Float)
            self.sound = M.QAudioBufferInput(self.audio_format)
            self.session.setAudioBufferInput(self.sound)
        self.recorder = M.QMediaRecorder()
        self.session.setRecorder(self.recorder)
        self._configure(output_size)
        self._connections = [(self.recorder.errorOccurred, self._on_error),
                             (self.recorder.recorderStateChanged, self._on_state),
                             (self.video.readyToSendVideoFrame, self._video_ready)]
        if self.sound is not None:
            self._connections.append((self.sound.readyToSendAudioBuffer, self._audio_ready))
        for signal, slot in self._connections:
            signal.connect(slot)

    def _configure(self, output_size: tuple[int, int]) -> None:
        M = self.M
        media = M.QMediaFormat(M.QMediaFormat.FileFormat.MPEG4)  # not Matroska: Qt has no H.264 there
        media.setVideoCodec(M.QMediaFormat.VideoCodec.H264)
        if self.has_audio:
            media.setAudioCodec(M.QMediaFormat.AudioCodec.AAC)
            self.recorder.setAudioSampleRate(SAMPLE_RATE)
            self.recorder.setAudioChannelCount(CHANNELS)
            self.recorder.setAudioBitRate(AUDIO_BITRATE)
        self.recorder.setMediaFormat(media)
        self.recorder.setQuality(M.QMediaRecorder.Quality.HighQuality)
        self.recorder.setVideoResolution(*output_size)
        self.recorder.setVideoFrameRate(self.fps)
        self.recorder.setOutputLocation(QUrl.fromLocalFile(str(self.path)))

    @property
    def is_ready(self) -> bool:
        """Qt has opened every input's encoder (`ready` has been emitted)."""
        return self._ready_inputs >= self._inputs

    @property
    def recording(self) -> bool:
        return self.recorder.recorderState() == self.M.QMediaRecorder.RecorderState.RecordingState

    def start(self) -> None:
        self.recorder.record()
        if self.recorder.error() != self.M.QMediaRecorder.Error.NoError:
            raise EncoderError(self.recorder.errorString() or "the recorder wouldn't start")

    def add_frame(self, index: int, image: QImage) -> None:
        """Frame number `index` (shown from index/fps seconds). Frame 0 must come first."""
        frame = self.M.QVideoFrame(image)
        frame.setStartTime(round(index * 1_000_000 / self.fps))
        frame.setEndTime(round((index + 1) * 1_000_000 / self.fps))
        self._pending_video.append(frame)
        while len(self._pending_video) > self.max_pending:
            # The encoder is behind: skip the oldest frame rather than fall further back. Never the
            # first one, though: Qt measures every timestamp from it, so losing it would shift the video.
            del self._pending_video[0 if self._first_sent else 1]
            self.frames_dropped += 1
        self._send_video()

    def add_audio(self, first: int, samples: array.array) -> None:
        """Interleaved float32 stereo, starting `first` samples into the recording."""
        if self.sound is None or not samples:
            return
        self._pending_audio.append(self.M.QAudioBuffer(QByteArray(samples.tobytes()), self.audio_format,
                                                        round(first * 1_000_000 / SAMPLE_RATE)))
        self._send_audio()

    @property
    def waiting(self) -> int:
        """Frames and sound buffers not yet taken by the encoder."""
        return len(self._pending_video) + len(self._pending_audio)

    def finish(self) -> None:
        """Hand over what's waiting, then close the file; `finished` (or `failed`) follows."""
        self._finishing = True
        self._maybe_stop()

    def _video_ready(self) -> None:
        self._input_ready("video")
        self._send_video()

    def _audio_ready(self) -> None:
        self._input_ready("audio")
        self._send_audio()

    def _input_ready(self, which: str) -> None:
        if which not in self._ready_inputs:
            self._ready_inputs.add(which)
            if self.is_ready:
                self.ready.emit()

    def _send_video(self) -> None:
        while self._pending_video and self.video.sendVideoFrame(self._pending_video[0]):
            self._pending_video.popleft()
            self.frames_sent += 1
            self._first_sent = True
        self._maybe_stop()

    def _send_audio(self) -> None:
        while self._pending_audio and self.sound.sendAudioBuffer(self._pending_audio[0]):
            self._pending_audio.popleft()
            self.buffers_sent += 1
        self._maybe_stop()

    def _maybe_stop(self) -> None:
        if self._finishing and not self.waiting and self.recording:
            self.recorder.stop()

    def _on_error(self, _error, message: str) -> None:
        self.error = message or "the recorder failed"
        self.failed.emit(self.error)

    def _on_state(self, state) -> None:
        if state == self.M.QMediaRecorder.RecorderState.StoppedState and self._finishing and not self.error:
            self.finished.emit()

    def close(self) -> None:
        """Disconnect and stop: nothing keeps the recorder, or its file, alive afterwards."""
        for signal, slot in self._connections:
            signal.disconnect(slot)
        self._connections.clear()
        if self.recorder.recorderState() != self.M.QMediaRecorder.RecorderState.StoppedState:
            self.recorder.stop()
