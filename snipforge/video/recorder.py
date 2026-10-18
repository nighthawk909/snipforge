"""Recording: the screen area, the pointer, the microphone and the computer's sound, into one MP4.

    screen (DXGI) + pointer ─ ScreenFrames thread ─┐
                                                   ├─ main thread ─ Encoder (Qt) ─► .mp4
    microphone + system sound (WASAPI) ─ Mixer ────┘

Everything is placed by one Clock (QueryPerformanceCounter time, pauses taken out), so sound and
picture stay together through pauses, stalls and dropped frames. A Recorder lives on the Qt main
thread: a QTimer hands frames and mixed sound to the encoder as they come. The clock starts once the
encoder is ready for frames ("starting" until then), so a slow start loses nothing.

A sound source that can't be opened (no microphone, Windows' privacy setting) becomes a warning and
the recording goes on without it; one that disappears mid-recording likewise. The screen and the
encoder are essential: if either fails, so does the recording. On stop, sound still on its way is
waited for, the file is closed, and then checked with FFmpeg before `finished` is emitted.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from .clock import Clock
from .cursor import PointerEffects
from .encoder import Encoder, EncoderError, fit
from .mixer import HOLD_BACK, Mixer

log = logging.getLogger(__name__)

PUMP_MS = 10
START_DELAY = 0.05  # seconds from the encoder being ready to time zero, so frame 0 is due at once
ENCODER_START_TIMEOUT = 15.0  # seconds; Qt's encoder takes ~0.1 s, over 1 s on a busy machine
DEFAULT_MAX_SIZE = (1920, 1080)  # the plan's default: recordings at most 1080p


class RecordingError(RuntimeError):
    """The recording couldn't start, or failed; the message is for the person recording."""


@dataclass(frozen=True)
class Settings:
    area: tuple[int, int, int, int]  # x, y, width, height in physical desktop pixels, on one monitor
    output: Path
    fps: int = 30
    max_size: tuple[int, int] | None = DEFAULT_MAX_SIZE  # None: the area's own size
    microphone: bool = True
    microphone_id: str | None = None  # None: Windows' default
    system_audio: bool = True
    speakers_id: str | None = None
    effects: PointerEffects = field(default_factory=PointerEffects)


@dataclass
class Result:
    path: Path
    duration: float  # seconds recorded (pauses left out)
    stats: dict


class Recorder(QObject):
    """One recording. start(), then pause()/resume() as wanted, then stop(); `finished` or `failed`
    follows. `warning` reports problems the recording carries on through."""

    state_changed = Signal(str)  # "starting", "recording", "paused", "finishing", "finished", "failed"
    warning = Signal(str)
    finished = Signal(object)  # Result
    failed = Signal(str)

    def __init__(self, settings: Settings, *, screen_frames: Callable | None = None,
                 capture: Callable | None = None, overlay: Callable | None = None,
                 encoder: Callable | None = None, now: Callable[[], float] = time.perf_counter) -> None:
        super().__init__()
        self.settings, self.now = settings, now
        self.clock = Clock()
        self.mixer = Mixer(self.clock, now=now)
        self.state = "idle"
        self._make_screen_frames = screen_frames or _real_screen_frames
        self._make_capture = capture or _real_capture
        self._make_overlay = overlay or _real_overlay
        self._make_encoder = encoder or Encoder
        self._captures: list = []
        self._frames = None
        self._encoder: Encoder | None = None
        self._stop_media = 0.0
        self._paused_media = 0.0  # recording time when the current pause began
        self._pump = QTimer(self)
        self._pump.timeout.connect(self._tick)

    # --- control ---------------------------------------------------------------------------------

    def start(self) -> None:
        """Open everything; raises RecordingError if the screen or the encoder can't be used (sound
        sources that can't be opened only warn). Recording begins ("recording") once the encoder is
        ready, or fails if it never is."""
        if self.state != "idle":
            raise RecordingError("This recording has already started")
        s = self.settings
        self._open_sound()
        frame_size = (s.area[2], s.area[3])
        try:
            self._encoder = self._make_encoder(s.output, frame_size, fit(frame_size, s.max_size), s.fps,
                                               audio=bool(self._captures))
            self._encoder.failed.connect(self._fail)
            self._encoder.finished.connect(self._done)
            self._encoder.ready.connect(self._begin)
            self._frames = self._make_screen_frames(s.area, s.fps, self.clock, self._make_overlay(s))
            self._frames.start()
            self._encoder.start()
        except (OSError, ValueError, EncoderError) as exc:
            self._close()
            raise RecordingError(f"The recording couldn't start: {exc}") from exc
        self._set_state("starting")
        QTimer.singleShot(int(ENCODER_START_TIMEOUT * 1000), self, self._check_started)
        if self._encoder.is_ready:
            self._begin()

    def _begin(self) -> None:
        if self.state == "starting":
            self.clock.begin(self.now() + START_DELAY)
            self._pump.start(PUMP_MS)
            self._set_state("recording")

    def _check_started(self) -> None:
        if self.state == "starting":
            self._fail(f"The video encoder didn't start within {ENCODER_START_TIMEOUT:g} seconds")
            self._discard_output()

    def pause(self) -> None:
        if self.state == "recording":
            now = self.now()
            self._paused_media = max(0.0, self.clock.media_time(now) or 0.0)
            self.clock.pause(now)
            self._set_state("paused")

    def resume(self) -> None:
        if self.state == "paused":
            self.clock.resume(self.now())
            self._set_state("recording")

    def stop(self) -> None:
        """Finish the recording: `finished` (or `failed`) follows once the file is complete."""
        if self.state == "starting":  # nothing recorded yet
            self._fail("The recording was stopped before it began")
            self._discard_output()
            return
        if self.state not in ("recording", "paused"):
            return
        self._stop_media = self.media_time()
        if self.state == "paused":
            self.clock.resume(self.now())  # the pause ends with the recording, at the same moment
        self._set_state("finishing")
        self._frames.stop()
        QTimer.singleShot(int(HOLD_BACK * 1000), self._finish_sound)  # sound still on its way

    def media_time(self) -> float:
        """Seconds recorded so far (pauses left out)."""
        if self.state in ("finishing", "finished", "failed"):
            return self._stop_media
        if self.state == "paused":
            return self._paused_media
        media = self.clock.media_time(self.now())
        return max(0.0, media or 0.0)

    def levels(self) -> dict[str, float]:
        """Each sound source's loudest moment since the last call (0-1), for level meters."""
        return self.mixer.levels()

    # --- inside ----------------------------------------------------------------------------------

    def _open_sound(self) -> None:
        s = self.settings
        wanted = [("microphone", s.microphone, s.microphone_id), ("system", s.system_audio, s.speakers_id)]
        for kind, on, device_id in wanted:
            if not on:
                continue
            self.mixer.add_source(kind)
            capture = self._make_capture(kind, device_id, self._packet_handler(kind), self._sound_lost)
            try:
                capture.start()
                self._captures.append(capture)
            except Exception as exc:  # AudioUnavailable, MicrophoneBlocked: record without it
                log.info("Recording without the %s: %s", kind, exc)
                self.warning.emit(str(exc))

    def _packet_handler(self, kind: str):
        def push(payload, frames, stamp, glitch):
            self.mixer.push(kind, payload, frames, stamp, glitch)
        return push

    def _sound_lost(self, error) -> None:  # on a capture thread: hand it to the main thread
        QTimer.singleShot(0, self, lambda: self.warning.emit(str(error)))

    def _tick(self) -> None:
        encoder = self._encoder
        if self._frames.error is not None and self.state in ("recording", "paused"):
            self._fail(f"The screen can't be recorded: {self._frames.error}")
            return
        for index, image in self._frames.take():
            encoder.add_frame(index, image)
        if self.state in ("recording", "paused") and encoder.has_audio:
            block = self.mixer.pull_ready(self.now())
            if block:
                encoder.add_audio(*block)

    def _finish_sound(self) -> None:
        if self.state != "finishing":
            return
        for capture in self._captures:
            capture.stop()
        self._tick()  # the last frames
        if self._encoder.has_audio:
            block = self.mixer.pull(self._stop_media)
            if block:
                self._encoder.add_audio(*block)
        self._encoder.finish()

    def _done(self) -> None:
        if self.state != "finishing":
            return
        self._pump.stop()
        stats = self.stats()
        self._close()
        problem = _check_file(self.settings.output, self._stop_media, bool(self._captures))
        if problem:
            self._fail(problem)
            return
        self._set_state("finished")
        self.finished.emit(Result(self.settings.output, self._stop_media, stats))

    def _fail(self, message: str) -> None:
        if self.state in ("finished", "failed"):
            return
        log.error("Recording failed: %s", message)
        self._pump.stop()
        self._close()
        self._set_state("failed")
        self.failed.emit(message)

    def _close(self) -> None:
        if self._frames is not None:
            self._frames.stop()
        for capture in self._captures:
            capture.stop()
        if self._encoder is not None:
            self._encoder.close()

    def _discard_output(self, tries: int = 10) -> None:
        """Delete the file of a recording that never began (Qt may hold it open a moment longer)."""
        try:
            self.settings.output.unlink(missing_ok=True)
        except OSError as exc:
            if tries:
                QTimer.singleShot(300, self, lambda: self._discard_output(tries - 1))
            else:
                log.warning("Couldn't delete the unused recording file %s: %s", self.settings.output, exc)

    def _set_state(self, state: str) -> None:
        self.state = state
        self.state_changed.emit(state)

    def stats(self) -> dict:
        """What happened, for the log and for spotting trouble (dropped frames, late sound)."""
        frames, encoder = self._frames, self._encoder
        sources = {name: {"placed_s": round(src.samples / self.mixer.rate, 2), "late_frames": src.late_frames,
                          "resyncs": src.resyncs, "bad_stamps": src.bad_stamps, "adjustments": src.adjustments}
                   for name, src in self.mixer.sources.items()}
        return {"frames_made": frames.made if frames else 0,
                "frames_skipped_late": frames.late_skipped if frames else 0,
                "frames_dropped_queue": frames.queue_dropped if frames else 0,
                "frames_dropped_encoder": encoder.frames_dropped if encoder else 0,
                "frames_sent": encoder.frames_sent if encoder else 0,
                "screen_reopens": frames.reopens if frames else 0,
                "protected_content": frames.protected_content if frames else False,
                "sound": sources}


def _check_file(path: Path, seconds: float, sound: bool) -> str:
    """A reason the finished file isn't what was recorded, or ''."""
    from . import ffmpeg

    try:
        info = ffmpeg.probe(path)
    except ffmpeg.FFmpegError as exc:
        return f"The recording was saved but can't be read back: {exc}"
    video = info.first("video")
    if video is None or video.codec != "h264":
        return "The recording was saved without H.264 video"
    if sound and (info.first("audio") is None or info.first("audio").codec != "aac"):
        return "The recording was saved without its sound"
    if info.duration is None or abs(info.duration - seconds) > max(1.0, seconds * 0.05):
        return f"The recording is {info.duration} s long, not the {seconds:.1f} s recorded"
    return ""


def _real_screen_frames(area, fps, clock, overlay):
    from .screen_frames import ScreenFrames

    return ScreenFrames(area, fps, clock, overlay)


def _real_capture(kind, device_id, on_packet, on_error):
    from . import wasapi

    return wasapi.WasapiCapture(wasapi.MICROPHONE if kind == "microphone" else wasapi.SYSTEM, on_packet,
                                device_id=device_id, on_error=on_error)


def _real_overlay(settings: Settings):
    from .cursor import PointerOverlay

    overlay = PointerOverlay(settings.area, settings.effects)
    overlay.prewarm()
    return overlay
