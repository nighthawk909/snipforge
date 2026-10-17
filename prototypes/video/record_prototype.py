"""Throwaway prototype: record an area of the screen with microphone + system audio to MP4.

Proves the stack recommended in docs/VIDEO-PLAN.md on a real PC:

    DXGI Desktop Duplication (area only)  ─┐
    pointer, highlight, click rings        ├─ QPainter ─► QVideoFrameInput ─┐
    webcam picture-in-picture (QCamera)   ─┘                                ├─► QMediaRecorder ─► MP4
    WASAPI microphone ─┐                                                    │   (Qt's own LGPL FFmpeg:
    WASAPI loopback   ─┴─ mixer (QPC timestamps) ─► QAudioBufferInput ──────┘    H.264 via Media Foundation, AAC)

Run from the repository root (needs PySide6 with QtMultimedia, i.e. PySide6-Addons, which the dev
environment has):

    python prototypes/video/record_prototype.py                 # 10 s of a 1280x720 area, mic + system audio
    python prototypes/video/record_prototype.py --choose        # pick the area with SnipForge's own overlay
    python prototypes/video/record_prototype.py --selftest      # flashes + beeps, then measures A/V sync
    python prototypes/video/record_prototype.py --pause-at 3 --resume-at 6 --webcam

Nothing here is the final design: one file, no tests, no error UI. See prototypes/video/README.md.
"""
from __future__ import annotations

import argparse
import array
import ctypes
import ctypes.wintypes
import math
import os
import queue
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))  # the repository, for --choose

from cursor import PointerOverlay  # noqa: E402
from dxgi_capture import AreaDuplicator  # noqa: E402
from mixer import Mixer  # noqa: E402
from PySide6 import QtMultimedia as M  # noqa: E402
from PySide6.QtCore import QByteArray, QRect, QRectF, QSize, Qt, QTimer, QUrl  # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPainterPath  # noqa: E402
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QWidget  # noqa: E402
from trim_spike import trim as remux  # noqa: E402
from wasapi_capture import CHANNELS, SAMPLE_RATE, WasapiCapture  # noqa: E402

WDA_EXCLUDEFROMCAPTURE = 0x11
MAX_PENDING_FRAMES = 6
AUDIO_LATENCY = 0.25  # seconds of audio held back so late packets still land in place
FLASHES = (2.0, 5.0, 8.0)  # self-test: seconds at which the box flashes and the beep sounds
BEEP_SECONDS = 0.3
MAGENTA = QColor(255, 0, 255)


def exclude_from_capture(widget: QWidget) -> bool:
    """Windows 10 2004+: the window stays visible to the person but is left out of every capture."""
    return bool(ctypes.windll.user32.SetWindowDisplayAffinity(int(widget.winId()), WDA_EXCLUDEFROMCAPTURE))


# ---------------------------------------------------------------------------------------------
# Choosing the area


def default_area() -> QRect:
    screen = QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio()
    g = screen.geometry()  # Qt keeps each screen's top-left in physical pixels
    w, h = min(1280, int(g.width() * dpr)), min(720, int(g.height() * dpr))
    return QRect(g.x() + (int(g.width() * dpr) - w) // 2, g.y() + (int(g.height() * dpr) - h) // 2, w, h)


def choose_area(app: QApplication) -> QRect | None:
    """SnipForge's own freeze-frame overlay, with its result read as a rectangle instead of an image.
    The real feature gives _ScreenOverlay a `selected_rect` signal instead of this patch."""
    from snipforge import capture

    picked: dict[str, QRect] = {}
    original = capture._ScreenOverlay._emit

    def emit(self, sel):
        physical = self._to_physical(sel)
        picked["rect"] = physical.translated(self.geometry().topLeft())
        original(self, sel)

    capture._ScreenOverlay._emit = emit
    session = capture.CaptureSession(lambda _image: app.exit(0))
    session.start()
    app.exec()
    return picked.get("rect")


# ---------------------------------------------------------------------------------------------
# Self-test pattern: a box that flashes while a beep plays, to measure A/V sync in the file


class Pattern(QWidget):
    def __init__(self, area_logical: QRect) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setGeometry(area_logical)
        self.flash = False
        self.start = time.perf_counter()

    def paintEvent(self, _e) -> None:  # noqa: N802 - Qt API
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(30, 34, 48))
        p.fillRect(self.flash_box(), QColor(255, 255, 255) if self.flash else QColor(10, 10, 10))
        p.setPen(QColor(220, 220, 220))
        p.setFont(QFont("Segoe UI", 28))
        p.drawText(self.rect().adjusted(24, 16, 0, 0), Qt.AlignLeft | Qt.AlignTop,
                   f"SnipForge video prototype  {time.perf_counter() - self.start:6.2f} s")

    def flash_box(self) -> QRect:
        return QRect(self.width() // 2 - 120, self.height() // 2 - 90, 240, 180)


class Beeper:
    """Keeps one output stream open, topped up with ~40 ms of silence, and swaps a beep in on
    demand. (Starting a QAudioSink per beep took ~0.6 s on the test PC, which made the sound
    really play late and the A/V measurement meaningless.)"""

    RATE = 48000
    AHEAD = 0.04  # seconds queued in the sink

    def __init__(self) -> None:
        fmt = M.QAudioFormat()
        fmt.setSampleRate(self.RATE)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(M.QAudioFormat.SampleFormat.Int16)
        self.sink = M.QAudioSink(M.QMediaDevices.defaultAudioOutput(), fmt)
        self.sink.setBufferSize(int(self.RATE * 2 * self.AHEAD))
        self.tone = array.array("h", (int(9000 * math.sin(2 * math.pi * 1000 * i / self.RATE))
                                      for i in range(int(self.RATE * BEEP_SECONDS)))).tobytes()
        self.pending = b""
        self.io = self.sink.start()
        self.timer = QTimer()
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._feed)
        self.timer.start(5)

    def beep(self) -> None:
        self.pending = self.tone
        self._feed()

    def _feed(self) -> None:
        free = self.sink.bytesFree() // 2 * 2
        if free <= 0:
            return
        chunk = self.pending[:free]
        self.pending = self.pending[len(chunk):]
        self.io.write(chunk + bytes(free - len(chunk)))

    def stop(self) -> None:
        self.timer.stop()
        self.sink.stop()


class PointerDemo:
    """Moves the real pointer in a circle over the test pattern (our own window) and clicks it at
    3 s and 6 s, so the recording shows the pointer, its highlight and click rings. Puts it back after."""

    def __init__(self, rec: Recorder) -> None:
        self.rec = rec
        self.timer = QTimer()
        self.timer.timeout.connect(self._step)
        self.clicked: set[int] = set()
        point = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        self.home = (point.x, point.y)

    def start(self) -> None:
        self.timer.start(15)

    def _step(self) -> None:
        t = self.rec.media_now()
        if t < 0:
            return
        a = self.rec.area
        cx, cy = a.x() + a.width() * 0.3, a.y() + a.height() * 0.6
        ctypes.windll.user32.SetCursorPos(int(cx + 150 * math.cos(t * 2)), int(cy + 100 * math.sin(t * 2)))
        for when in (3, 6):
            if t >= when and when not in self.clicked:
                self.clicked.add(when)
                ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # left down
                ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # left up
        if t >= self.rec.args.seconds - 0.5 or self.rec.stopping:
            self.timer.stop()
            ctypes.windll.user32.SetCursorPos(*self.home)


# ---------------------------------------------------------------------------------------------
# The floating control bar (left out of the recording by Windows)


class ControlBar(QWidget):
    def __init__(self, recorder: Recorder) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.recorder = recorder
        self.setStyleSheet("QWidget { background: #ff00ff; color: white; font: 11pt 'Segoe UI'; }"
                           "QPushButton { background: #333; border-radius: 4px; padding: 4px 10px; }")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        self.dot = QLabel("●")
        self.time = QLabel("00:00")
        self.meters = QLabel("")
        self.pause = QPushButton("Pause")
        self.stop = QPushButton("Stop")
        for w in (self.dot, self.time, self.meters, self.pause, self.stop):
            row.addWidget(w)
        self.pause.clicked.connect(recorder.toggle_pause)
        self.stop.clicked.connect(recorder.stop)
        self.adjustSize()

    def refresh(self, seconds: float, levels: dict[str, float], paused: bool) -> None:
        """Called ~10 times a second. Only touches what changed: restyling or resizing a widget on
        every tick stalled the main thread for up to 1.2 s, and with it (through the GIL) the capture."""
        def bar(level: float) -> str:
            n = min(10, round(10 * level ** 0.5))
            return "▮" * n + "▯" * (10 - n)
        self.time.setText(f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}")
        self.meters.setText("  ".join(f"{name} {bar(level)}" for name, level in levels.items()))
        if paused != getattr(self, "_paused", None):
            self._paused = paused
            self.dot.setStyleSheet("color: #888" if paused else "color: #ff3b30")
            self.pause.setText("Resume" if paused else "Pause")
            self.adjustSize()


# ---------------------------------------------------------------------------------------------
# Webcam: frames from QCamera, drawn as a rounded picture-in-picture in a corner of each frame


class Webcam:
    def __init__(self) -> None:
        self.session = M.QMediaCaptureSession()
        self.camera = M.QCamera(M.QMediaDevices.defaultVideoInput())
        self.sink = M.QVideoSink()
        self.session.setCamera(self.camera)
        self.session.setVideoSink(self.sink)
        self.latest: QImage | None = None
        self._lock = threading.Lock()
        self.frames = 0
        self.sink.videoFrameChanged.connect(self._frame)
        self.camera.start()

    def _frame(self, frame: M.QVideoFrame) -> None:
        image = frame.toImage().scaledToWidth(320, Qt.SmoothTransformation)
        with self._lock:
            self.latest = image
            self.frames += 1

    def image(self) -> QImage | None:
        with self._lock:
            return self.latest

    def stop(self) -> None:
        self.camera.stop()


def draw_webcam(frame: QImage, cam: QImage) -> None:
    w = min(320, frame.width() // 4)
    h = w * cam.height() // max(1, cam.width())
    target = QRectF(frame.width() - w - 24, frame.height() - h - 24, w, h)
    p = QPainter(frame)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(target, 16, 16)
    p.setClipPath(path)
    p.drawImage(target, cam)
    p.end()


# ---------------------------------------------------------------------------------------------
# The recorder


class Recorder:
    def __init__(self, app: QApplication, area: QRect, args) -> None:
        self.app, self.args = app, args
        self.area = QRect(area.x(), area.y(), area.width() // 2 * 2, area.height() // 2 * 2)  # H.264 wants even sizes
        self.fps = args.fps
        self.out = Path(args.out).resolve()
        # Recorded as Matroska, which survives a crash (an MP4 is unreadable until it is closed),
        # then copied into MP4 when the recording stops (no re-encoding; milliseconds).
        self.raw = self.out.with_suffix(".recording.mkv")
        self.stats = {"frames": 0, "sent": 0, "dropped": 0, "screen_updates": 0, "audio_buffers": 0,
                      "late": 0, "queue_full": 0, "encoder_behind": 0, "pump_gap_ms": 0.0, "frame_ms": 0.0}
        self._last_pump = 0.0
        self.paused = False
        self.stopping = False
        self.finished = False
        self._frames: queue.Queue = queue.Queue(maxsize=16)
        self._pending_video: list[M.QVideoFrame] = []
        self._pending_audio: list[M.QAudioBuffer] = []

        self.session = M.QMediaCaptureSession()
        vfmt = M.QVideoFrameFormat(QSize(self.area.width(), self.area.height()),
                                   M.QVideoFrameFormat.PixelFormat.Format_BGRA8888)
        vfmt.setStreamFrameRate(self.fps)
        self.video_in = M.QVideoFrameInput(vfmt)
        self.audio_format = M.QAudioFormat()
        self.audio_format.setSampleRate(SAMPLE_RATE)
        self.audio_format.setChannelCount(CHANNELS)
        self.audio_format.setSampleFormat(M.QAudioFormat.SampleFormat.Float)
        self.audio_in = M.QAudioBufferInput(self.audio_format)
        self.recorder = M.QMediaRecorder()
        self.session.setVideoFrameInput(self.video_in)
        self.session.setAudioBufferInput(self.audio_in)
        self.session.setRecorder(self.recorder)
        fmt = M.QMediaFormat(M.QMediaFormat.FileFormat.Matroska)
        fmt.setVideoCodec(M.QMediaFormat.VideoCodec.H264)
        fmt.setAudioCodec(M.QMediaFormat.AudioCodec.AAC)
        self.recorder.setMediaFormat(fmt)
        self.recorder.setQuality(M.QMediaRecorder.Quality.HighQuality)
        out_w, out_h = self.output_size(args.max_width)
        self.recorder.setVideoResolution(out_w, out_h)  # the encoder scales, in the same pass as YUV conversion
        self.recorder.setVideoFrameRate(self.fps)
        self.recorder.setAudioSampleRate(SAMPLE_RATE)
        self.recorder.setAudioChannelCount(CHANNELS)
        self.recorder.setAudioBitRate(160_000)
        self.recorder.setOutputLocation(QUrl.fromLocalFile(str(self.raw)))
        self.recorder.errorOccurred.connect(lambda e, s: print(f"Recorder error {e}: {s}"))
        self.recorder.recorderStateChanged.connect(self._state)
        self.video_in.readyToSendVideoFrame.connect(self._send_video)
        self.audio_in.readyToSendAudioBuffer.connect(self._send_audio)

        self.start = 0.0
        self.mixer: Mixer | None = None
        self.captures: list[WasapiCapture] = []
        self.webcam = Webcam() if args.webcam else None
        self.pointer = PointerOverlay(self.area.x(), self.area.y(),
                                      highlight=not args.no_effects, rings=not args.no_effects)
        self._thread: threading.Thread | None = None
        self._stop_video = threading.Event()
        self._video_ready = threading.Event()
        self.bar = ControlBar(self)
        self.pump = QTimer()
        self.pump.setTimerType(Qt.PreciseTimer)
        self.pump.timeout.connect(self._pump)
        self.cpu_start = 0.0
        self.debug_audio: list[array.array] = []
        self.sent_log: list[tuple[int, float]] = []  # (frame index, wall time sent)

    def output_size(self, max_width: int) -> tuple[int, int]:
        w, h = self.area.width(), self.area.height()
        if max_width and w > max_width:
            w, h = max_width, round(h * max_width / w)
        return w // 2 * 2, h // 2 * 2

    # -- control ---------------------------------------------------------------------------
    def begin(self) -> None:
        # Open the audio devices first (~0.1-0.9 s); packets before the start are dropped.
        self.mixer = Mixer(SAMPLE_RATE, CHANNELS, float("inf"))
        if not self.args.no_mic:
            self.mixer.add_source("mic")
            self.captures.append(WasapiCapture(False, lambda *a: self.mixer.push("mic", *a)))
        if not self.args.no_system:
            self.mixer.add_source("system")
            self.captures.append(WasapiCapture(True, lambda *a: self.mixer.push("system", *a)))
        opened = time.perf_counter()
        for c in self.captures:
            c.start()
        self.audio_open_seconds = time.perf_counter() - opened
        self.cpu_start = time.process_time()
        self._thread = threading.Thread(target=self._video_loop, name="video", daemon=True)
        self._thread.start()
        self._video_ready.wait(3)  # the first frame is in hand, so frame 0 can be at 0.0 s
        self.recorder.record()
        self.start = self.mixer.start = time.perf_counter() + 0.05
        self.pump.start(10)
        self.bar.show()
        self.bar.excluded = exclude_from_capture(self.bar)

    def toggle_pause(self) -> None:
        now = time.perf_counter()
        if self.paused:
            self.mixer.resume(now)
        else:
            self.mixer.pause(now)
        self.paused = not self.paused
        print(f"{'Paused' if self.paused else 'Resumed'} at {now - self.start:.2f} s (wall)")

    def stop(self) -> None:
        if self.stopping:
            return
        self.stopping = True
        self.stop_media = self.media_now()
        self._stop_video.set()
        if self._thread:
            self._thread.join(2)
        for c in self.captures:
            c.stop()
        if self.webcam:
            self.webcam.stop()
        self.cpu_used = time.process_time() - self.cpu_start
        self.wall_used = time.perf_counter() - self.start
        self._drain_video()
        self._pull_audio(final=True)
        QTimer.singleShot(0, self._finish_when_sent)

    def media_now(self) -> float:
        t = self.mixer.media_time(time.perf_counter()) if self.mixer else None
        return t if t is not None else -1.0

    # -- video thread: duplicate the area, draw effects, queue frames at a steady rate ------
    def _video_loop(self) -> None:
        w, h = self.area.width(), self.area.height()
        latest: QImage | None = None
        try:
            dup = AreaDuplicator(self.area.x(), self.area.y(), w, h)
            while latest is None and not self._stop_video.is_set():
                got = dup.grab(200)  # the first call returns the current screen at once
                if got:
                    latest = QImage(got[0], w, h, got[1], QImage.Format.Format_ARGB32).copy()
        finally:
            self._video_ready.set()
        index = 0
        period = 1.0 / self.fps
        while not self._stop_video.is_set():
            media = self.mixer.media_time(time.perf_counter())
            if media is None or media < index * period:
                wait = period / 2 if media is None else min(period, index * period - media)
                got = dup.grab(max(1, int(wait * 1000)))
                if got:
                    latest = QImage(got[0], w, h, got[1], QImage.Format.Format_ARGB32).copy()
                    self.stats["screen_updates"] += 1
                continue
            if index and media - index * period > 3 * period:  # fell behind: skip ahead, keep timestamps true
                skipped = int(media / period) - index
                self.stats["dropped"] += skipped
                self.stats["late"] += skipped
                index += skipped
            made = time.perf_counter()
            frame = latest.copy()
            if self.webcam and (cam := self.webcam.image()) is not None:
                draw_webcam(frame, cam)
            self.pointer.paint(frame)
            self.stats["frame_ms"] = max(self.stats["frame_ms"], (time.perf_counter() - made) * 1000)
            try:
                self._frames.put((index, frame), timeout=0.5)
                self.stats["frames"] += 1
            except queue.Full:
                self.stats["dropped"] += 1
                self.stats["queue_full"] += 1
            index += 1
        self.reopened = dup.reopened
        dup.close()

    # -- main thread: hand frames and audio to Qt, honouring its back-pressure ---------------
    def _pump(self) -> None:
        now = time.perf_counter()
        if self._last_pump:
            self.stats["pump_gap_ms"] = max(self.stats["pump_gap_ms"], (now - self._last_pump) * 1000)
        self._last_pump = now
        if os.environ.get("DEBUG_STALLS"):
            import faulthandler
            faulthandler.dump_traceback_later(0.4, exit=False)  # re-armed every tick: fires only on a stall
        self._drain_video()
        self._pull_audio()
        if self.mixer and now - getattr(self, "_last_bar", 0.0) >= 0.1:
            self._last_bar = now
            self.bar.refresh(max(0.0, self.media_now()), self.mixer.levels(), self.paused)
        if not self.stopping and self.media_now() >= self.args.seconds:
            self.stop()
        if self.args.pause_at is not None and not self.paused and not self.stopping:
            if abs(self.media_now() - self.args.pause_at) < 0.02 and not getattr(self, "_did_pause", False):
                self._did_pause = True
                self.toggle_pause()
                QTimer.singleShot(int((self.args.resume_at - self.args.pause_at) * 1000), self.toggle_pause)

    def _drain_video(self) -> None:
        while True:
            try:
                index, image = self._frames.get_nowait()
            except queue.Empty:
                break
            if not hasattr(self, "first_index"):
                self.first_index = index
            frame = M.QVideoFrame(image)
            frame.setStartTime(int(index * 1_000_000 / self.fps))
            frame.setEndTime(int((index + 1) * 1_000_000 / self.fps))
            self._pending_video.append(frame)
            if len(self._pending_video) > MAX_PENDING_FRAMES:  # the encoder is behind: drop, never queue up
                self._pending_video.pop(0)
                self.stats["dropped"] += 1
                self.stats["encoder_behind"] += 1
        self._send_video()

    def _send_video(self) -> None:
        while self._pending_video and self.video_in.sendVideoFrame(self._pending_video[0]):
            self._pending_video.pop(0)
            self.stats["sent"] += 1

    def _pull_audio(self, final: bool = False) -> None:
        if not self.mixer:
            return
        upto = self.stop_media if final else self.media_now() - AUDIO_LATENCY
        if upto <= 0:
            return
        block = self.mixer.pull(int(upto * SAMPLE_RATE))
        if block:
            start, samples = block
            if self.args.debug_wav:
                self.debug_audio.append(samples)
            buf = M.QAudioBuffer(QByteArray(samples.tobytes()), self.audio_format, int(start * 1_000_000 / SAMPLE_RATE))
            self._pending_audio.append(buf)
        self._send_audio()

    def _send_audio(self) -> None:
        while self._pending_audio and self.audio_in.sendAudioBuffer(self._pending_audio[0]):
            self._pending_audio.pop(0)
            self.stats["audio_buffers"] += 1

    def _finish_when_sent(self) -> None:
        self._send_video()
        self._send_audio()
        if self._pending_video or self._pending_audio:
            QTimer.singleShot(20, self._finish_when_sent)
            return
        self.pump.stop()
        self.bar.hide()
        self.recorder.stop()

    def _state(self, state) -> None:
        if state == M.QMediaRecorder.RecorderState.StoppedState and self.stopping:
            t = time.perf_counter()
            remux(str(self.raw), str(self.out), 0.0, 1e9)
            self.remux_ms = (time.perf_counter() - t) * 1000
            if not os.environ.get("KEEP_MKV"):
                self.raw.unlink()
            self.finished = True
            QTimer.singleShot(100, self.app.quit)


# ---------------------------------------------------------------------------------------------
# Checking the file: decoded with Qt itself (the same libraries the editor would trim with)


def analyse(path: Path, flash_rect: QRect | None, fps: int) -> dict:
    app = QApplication.instance()
    result: dict = {"video_frames": 0, "flash_times": [], "magenta_frames": 0, "beep_times": [], "audio_seconds": 0.0}
    player = M.QMediaPlayer()
    sink = M.QVideoSink()
    player.setVideoSink(sink)
    lit = {"on": False}

    def frame(f: M.QVideoFrame) -> None:
        if not f.isValid():
            return
        result["video_frames"] += 1
        image = f.toImage()
        result.setdefault("size", (image.width(), image.height()))
        small = image.scaled(64, 36)
        result["magenta_frames"] += any(
            (c := small.pixelColor(x, y)).red() > 230 and c.green() < 40 and c.blue() > 230
            for x in range(0, 64, 4) for y in range(0, 36, 4))
        if flash_rect is not None:
            c = image.pixelColor(flash_rect.center())
            on = c.lightness() > 200
            if on and not lit["on"]:
                result["flash_times"].append(f.startTime() / 1e6)
            lit["on"] = on

    sink.videoFrameChanged.connect(frame)
    player.mediaStatusChanged.connect(lambda s: app.quit() if s == M.QMediaPlayer.MediaStatus.EndOfMedia else None)
    player.errorOccurred.connect(lambda e, s: (print("Player error", e, s), app.quit()))
    player.setSource(QUrl.fromLocalFile(str(path)))
    player.play()
    app.exec()
    result["duration"] = player.duration() / 1000
    result["has_audio"] = bool(player.audioTracks())
    result["has_video"] = bool(player.videoTracks())

    decoder = M.QAudioDecoder()
    fmt = M.QAudioFormat()
    fmt.setSampleRate(48000)
    fmt.setChannelCount(2)
    fmt.setSampleFormat(M.QAudioFormat.SampleFormat.Float)
    decoder.setAudioFormat(fmt)
    loud = {"on": False, "peak": 0.0, "sum2": 0.0, "n": 0}

    def ready() -> None:
        buf = decoder.read()
        samples = array.array("f")
        samples.frombytes(bytes(buf.constData())[: buf.byteCount()])
        t0 = buf.startTime() / 1e6
        result["audio_seconds"] = max(result["audio_seconds"], t0 + buf.frameCount() / 48000)
        window = 240 * 2  # 5 ms
        for i in range(0, len(samples) - window + 1, window):
            seg = samples[i:i + window]
            rms = math.sqrt(sum(v * v for v in seg) / window)
            loud["peak"] = max(loud["peak"], max(seg), -min(seg))
            on = rms > 0.05
            t = t0 + i / 2 / 48000
            if on and not loud["on"] and (not result["beep_times"] or t - result["beep_times"][-1] > 0.2):
                result["beep_times"].append(t)  # a glitch inside one beep doesn't count as a new one
            loud["on"] = on

    decoder.bufferReady.connect(ready)
    decoder.finished.connect(app.quit)
    try:
        decoder.error.connect(lambda e: (print("Decoder error", e, decoder.errorString()), app.quit()))
    except AttributeError:  # `error` is both a getter and a signal in Qt 6
        pass
    decoder.setSource(QUrl.fromLocalFile(str(path)))
    decoder.start()
    app.exec()
    result["audio_peak"] = loud["peak"]
    return result


def db(level: float) -> str:
    return f"{20 * math.log10(level):.1f} dBFS" if level > 0 else "silent"


# ---------------------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--max-width", type=int, default=0, help="scale the video down to this width (0: no scaling)")
    parser.add_argument("--area", help="x,y,width,height in physical desktop pixels (default: 1280x720, centred)")
    parser.add_argument("--choose", action="store_true", help="choose the area with SnipForge's capture overlay")
    parser.add_argument("--out", default=str(Path.home() / "Videos" / "snipforge-prototype.mp4"))
    parser.add_argument("--no-mic", action="store_true")
    parser.add_argument("--no-system", action="store_true")
    parser.add_argument("--no-effects", action="store_true", help="no highlight or click rings")
    parser.add_argument("--webcam", action="store_true", help="picture-in-picture from the default camera")
    parser.add_argument("--pause-at", type=float)
    parser.add_argument("--resume-at", type=float)
    parser.add_argument("--selftest", action="store_true", help="flash + beep inside the area, then measure A/V sync")
    parser.add_argument("--demo-pointer", action="store_true",
                        help="with --selftest: circle the pointer over the pattern and click twice (moves your mouse)")
    parser.add_argument("--debug-wav", action="store_true", help="also write the mixer's own output next to the MP4")
    args = parser.parse_args()
    if (args.pause_at is None) != (args.resume_at is None):
        parser.error("--pause-at and --resume-at go together")

    app = QApplication(sys.argv)
    if args.choose:
        area = choose_area(app)
        if area is None:
            print("Cancelled")
            return 1
    elif args.area:
        x, y, w, h = (int(v) for v in args.area.split(","))
        area = QRect(x, y, w, h)
    else:
        area = default_area()
    print(f"Recording {args.seconds:.0f} s of {area.width()}x{area.height()} at ({area.x()},{area.y()}), "
          f"{args.fps} fps -> {args.out}")

    rec = Recorder(app, area, args)
    pattern = beep = None
    if args.selftest:
        screen = QGuiApplication.screenAt(area.center())
        dpr = screen.devicePixelRatio() if screen else 1.0
        logical = QRect(area.x(), area.y(), round(rec.area.width() / dpr), round(rec.area.height() / dpr))
        pattern = Pattern(logical)
        pattern.show()
        anim = QTimer()
        anim.timeout.connect(pattern.update)
        anim.start(16)
        beep = Beeper()
        # the control bar sits inside the area here, to prove Windows leaves it out of the frames
        rec.bar.move(logical.x() + 20, logical.y() + logical.height() - 70)
    else:
        rec.bar.move(area.x(), max(0, area.y() - 48))

    called: list[float] = []  # seconds into the recording at which each flash + beep was started

    def schedule_flashes() -> None:
        for t in FLASHES:
            if t >= args.seconds:
                continue
            delay = max(0, int((rec.start + t - time.perf_counter()) * 1000))

            def on():
                pattern.flash = True
                pattern.repaint()
                beep.beep()
                called.append(rec.media_now())
                QTimer.singleShot(int(BEEP_SECONDS * 1000), off)

            def off():
                pattern.flash = False
                pattern.repaint()
            QTimer.singleShot(delay, on)

    QTimer.singleShot(400, rec.begin)
    if args.selftest:
        QTimer.singleShot(450, schedule_flashes)
    if args.selftest and args.demo_pointer:
        demo = PointerDemo(rec)
        QTimer.singleShot(500, demo.start)
    app.exec()
    if pattern:
        pattern.close()
        beep.stop()
    if not rec.finished:
        print("The recording didn't finish")
        return 1

    size = rec.out.stat().st_size if rec.out.exists() else 0
    cores = os.cpu_count() or 1
    s = rec.stats
    print(f"\nWrote {rec.out} ({size / 1e6:.1f} MB; recorded as MKV, copied into MP4 in {rec.remux_ms:.0f} ms)")
    print(f"Video: {s['frames']} frames made, {s['sent']} sent to the encoder, {s['dropped']} dropped; "
          f"{s['screen_updates']} screen updates; duplication reopened {getattr(rec, 'reopened', 0)}x")
    print(f"Drops: {s['late']} capture thread late, {s['queue_full']} queue full, "
          f"{s['encoder_behind']} encoder behind; longest main-thread gap {s['pump_gap_ms']:.0f} ms, "
          f"slowest frame {s['frame_ms']:.0f} ms")
    for c in rec.captures:
        src = rec.mixer.sources["mic" if not c.loopback else "system"]
        print(f"Audio {src.name}: {c.packets} packets, {src.samples / SAMPLE_RATE:.2f} s placed, "
              f"peak {db(src.max_peak)}, resyncs {src.resyncs}")
    print(f"Audio devices opened in {rec.audio_open_seconds:.2f} s")
    print(f"Control bar excluded from capture: {getattr(rec.bar, 'excluded', False)}")
    if rec.webcam:
        print(f"Webcam frames: {rec.webcam.frames}")
    print(f"Clicks seen by the pointer overlay: {rec.pointer.clicks_seen}")
    print(f"CPU: {rec.cpu_used:.1f} s over {rec.wall_used:.1f} s = {100 * rec.cpu_used / rec.wall_used / cores:.0f}% "
          f"of {cores} logical cores ({100 * rec.cpu_used / rec.wall_used:.0f}% of one)")

    if args.debug_wav and rec.debug_audio:
        import wave
        pcm = array.array("h", (int(max(-1.0, min(1.0, v)) * 32767) for block in rec.debug_audio for v in block))
        with wave.open(str(rec.out.with_suffix(".mixer.wav")), "wb") as wav:
            wav.setnchannels(CHANNELS)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(pcm.tobytes())
        print(f"Mixer output: {rec.out.with_suffix('.mixer.wav')} ({len(pcm) / CHANNELS / SAMPLE_RATE:.2f} s)")
    print(f"First video frame index sent: {rec.first_index}")

    flash_rect = None
    if args.selftest:
        flash_rect = QRect(rec.area.width() // 2 - 60, rec.area.height() // 2 - 40, 120, 80)
    result = analyse(rec.out, flash_rect, args.fps)
    print(f"\nFile check (decoded with Qt): {result.get('size')} video, {result['video_frames']} frames, "
          f"duration {result['duration']:.2f} s, audio track {result['has_audio']}, "
          f"audio {result['audio_seconds']:.2f} s, peak {db(result['audio_peak'])}")
    print(f"Frames showing the magenta control bar: {result['magenta_frames']}")
    if args.selftest:
        print(f"Flashes seen at  {[round(t, 3) for t in result['flash_times']]}")
        print(f"Beeps heard at   {[round(t, 3) for t in result['beep_times']]}")
        pairs = list(zip(result["flash_times"], result["beep_times"], strict=False))
        offsets = [round((b - f) * 1000) for f, b in pairs]
        print(f"Audio minus video (ms): {offsets}  (positive = sound later; includes the speakers' own latency)")
        print(f"Started at       {[round(t, 3) for t in called]}")
        picture = [round((f - c) * 1000) for f, c in zip(result["flash_times"], called, strict=False)]
        sound = [round((b - c) * 1000) for b, c in zip(result["beep_times"], called, strict=False)]
        print(f"Picture path (ms): {picture}  sound path (ms): {sound}  (from starting the flash + beep to the file)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
