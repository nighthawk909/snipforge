"""Recording: the screen area, the pointer, the microphone and the computer's sound, into one MP4.

    screen (DXGI) + pointer ─ ScreenFrames thread ─┐
                                                   ├─ main thread ─ Encoder (Qt) ─► .mp4
    microphone + system sound (WASAPI) ─ Mixer ────┘

Everything is placed by one Clock (QueryPerformanceCounter time, pauses taken out), so sound and
picture stay together through pauses, stalls and dropped frames. A Recorder lives on the Qt main
thread: a QTimer hands frames and mixed sound to the encoder as they come. The clock starts once the
encoder is ready for frames ("starting" until then), so a slow start loses nothing.

A sound source that can't be opened (no microphone, Windows' privacy setting) becomes a warning and
the recording goes on without it; one that disappears mid-recording likewise. If the screen is lost
mid-recording, what was recorded is kept and finished. The encoder is essential: if it fails, so
does the recording. On stop, sound still on its way is waited for, the file is closed, and then
checked with FFmpeg (recording_check.py) before `finished` is emitted. Nothing waits forever:
starting and finishing each have a time limit.

Before starting, the recorder checks the computer can record (support.py) and that the drive has
room; while recording it warns when the drive runs low, and stops (keeping the recording) before
it's full, since a recording that fills the drive can't be finished (disk_space.py).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal

from . import support
from .clock import Clock
from .cursor import PointerEffects
from .disk_space import LOW_SPACE, MB, MIN_FREE_TO_GO_ON, MIN_FREE_TO_START, SpaceWatch, free_bytes
from .encoder import Encoder, EncoderError, fit
from .mixer import HOLD_BACK, Mixer
from .recording_check import check_file

log = logging.getLogger(__name__)

PUMP_MS = 10
START_DELAY = 0.05  # seconds from the encoder being ready to time zero, so frame 0 is due at once
ENCODER_START_TIMEOUT = 15.0  # seconds; Qt's encoder takes ~0.1 s, over 1 s on a busy machine
FINISH_TIMEOUT = 30.0  # seconds from stop() to a finished file; it normally takes well under one
DEFAULT_MAX_SIZE = (1920, 1080)  # the plan's default: recordings at most 1080p
MIN_SIDE = 48  # pixels; Windows' H.264 encoder refuses pictures much smaller than this
MAX_FPS = 60
DISK_CHECK_SECONDS = 5.0
ACTIVE = ("starting", "recording", "paused")
OVER = ("finished", "failed", "cancelled")


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

    def __post_init__(self) -> None:
        _check_settings(self)

    @property
    def output_size(self) -> tuple[int, int]:
        """The recording's picture size: the area's, scaled down to fit max_size."""
        return fit((self.area[2], self.area[3]), self.max_size)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_settings(s: Settings) -> None:
    """ValueError, with a reason for the person recording, if `s` can't be recorded."""
    if len(s.area) != 4 or not all(_is_int(v) for v in s.area):
        raise ValueError("The area to record must be four whole numbers")
    if not _is_int(s.fps) or not 1 <= s.fps <= MAX_FPS:
        raise ValueError(f"Recordings can have 1 to {MAX_FPS} frames a second, not {s.fps}")
    if s.max_size is not None and (len(s.max_size) != 2
                                   or not all(_is_int(v) and v >= MIN_SIDE for v in s.max_size)):
        raise ValueError(f"The largest size must be two whole numbers of at least {MIN_SIDE}")
    if min(s.area[2], s.area[3]) < MIN_SIDE or min(s.output_size) < MIN_SIDE:
        raise ValueError(f"The area is too small or thin to record: at least {MIN_SIDE} pixels each way")


@dataclass
class Result:
    path: Path
    duration: float  # seconds recorded (pauses left out)
    stats: dict


class Recorder(QObject):
    """One recording. start(), then pause()/resume() as wanted, then stop(); `finished` or `failed`
    follows, or `cancelled` after cancel() (or a stop before anything was recorded). `warning`
    reports problems the recording carries on through. finish_now() finishes it there and then, for
    when Windows is about to sleep or end the session."""

    state_changed = Signal(str)  # "starting", "recording", "paused", "finishing", then one of OVER
    warning = Signal(str)
    finished = Signal(object)  # Result
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, settings: Settings, *, screen_frames: Callable | None = None,
                 capture: Callable | None = None, overlay: Callable | None = None,
                 encoder: Callable | None = None, free_space: Callable[[Path], int] | None = None,
                 now: Callable[[], float] = time.perf_counter) -> None:
        super().__init__()
        self.settings, self.now = settings, now
        self.clock = Clock()
        self.mixer = Mixer(self.clock, now=now)
        self.state = "idle"
        self._make_screen_frames = screen_frames or _real_screen_frames
        self._make_capture = capture or _real_capture
        self._make_overlay = overlay or _real_overlay
        self._make_encoder = encoder or Encoder
        self.free_space = free_space or free_bytes  # bytes free in a folder
        self._space: SpaceWatch | None = None
        self._space_warned = False
        self._captures: list = []
        self._frames = None
        self._encoder: Encoder | None = None
        self._output_existed = False  # a file already there is never deleted
        self._held = False  # started with hold=True, and go() not called yet
        self._overlay_warned = False
        self._stop_media = 0.0
        self._paused_media = 0.0  # recording time when the current pause began
        self._pump = QTimer(self)
        self._pump.timeout.connect(self._tick)

    # --- control ---------------------------------------------------------------------------------

    def start(self, hold: bool = False) -> None:
        """Open everything; raises RecordingError if this computer can't record, the drive has no
        room, or the screen or the encoder can't be used (sound sources that can't be opened only
        warn). Recording begins ("recording") once the encoder is ready, or fails if it never is.
        `hold`: open everything now, but begin only at go() (a countdown runs meanwhile, so the
        recording starts as it ends)."""
        if self.state != "idle":
            raise RecordingError("This recording has already started")
        self._check_can_record()
        self._held = hold
        self._output_existed = self.settings.output.exists()
        try:
            self._open()
        except Exception as exc:
            if not isinstance(exc, (OSError, ValueError, EncoderError)):
                log.exception("Starting the recording failed")
            self._close(discard=True)
            raise RecordingError(f"The recording couldn't start: {exc}") from exc
        self._space = SpaceWatch(self.settings.output.parent, self.free_space, DISK_CHECK_SECONDS)
        self._space.start()
        self._set_state("starting")
        self._pump.start(PUMP_MS)  # also notices a screen lost while the encoder warms up
        QTimer.singleShot(int(ENCODER_START_TIMEOUT * 1000), self, self._check_started)
        if self._encoder.is_ready:
            self._begin()

    def go(self) -> None:
        """Begin a recording started with hold=True: now, or as soon as the encoder is ready."""
        self._held = False
        if self.state == "starting" and self._encoder.is_ready:
            self._begin()

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
        """Finish the recording: `finished` (or `failed`) follows once the file is complete. Stopped
        before a frame was recorded, it's cancelled instead."""
        if self.state == "starting" or (self.state in ACTIVE and self.media_time() < 1 / self.settings.fps):
            self.cancel()
            return
        if self.state not in ACTIVE:
            return
        self._stop_media = self.media_time()
        if self.state == "paused":
            self.clock.resume(self.now())  # the pause ends with the recording, at the same moment
        self._set_state("finishing")
        self._frames.stop()
        QTimer.singleShot(int(HOLD_BACK * 1000), self, self._finish_sound)  # sound still on its way
        QTimer.singleShot(int(FINISH_TIMEOUT * 1000), self, self._check_finished)

    def cancel(self) -> None:
        """Stop and keep nothing: the file is deleted and `cancelled` follows."""
        if self.state in ACTIVE or self.state == "finishing":
            self._end("cancelled", discard=True)
            self.cancelled.emit()

    def finish_now(self, timeout: float) -> bool:
        """stop(), then run Qt's events until the file is finished or `timeout` seconds have passed:
        for when Windows is about to sleep or end the session. True if nothing more needs doing."""
        self.stop()
        if self.state == "finishing":
            loop = QEventLoop()
            quit_loop = lambda _state: loop.quit()  # noqa: E731 - kept to disconnect it
            self.state_changed.connect(quit_loop)  # "finishing" only ever changes to an end
            QTimer.singleShot(int(timeout * 1000), loop, loop.quit)
            loop.exec()
            self.state_changed.disconnect(quit_loop)
        return self.state in ("finished", "cancelled", "failed")

    def media_time(self) -> float:
        """Seconds recorded so far (pauses left out)."""
        if self.state == "finishing" or self.state in OVER:
            return self._stop_media
        if self.state == "paused":
            return self._paused_media
        media = self.clock.media_time(self.now()) if self.state == "recording" else None
        return max(0.0, media or 0.0)

    def levels(self) -> dict[str, float]:
        """Each sound source's loudest moment since the last call (0-1), for level meters."""
        return self.mixer.levels()

    # --- starting --------------------------------------------------------------------------------

    def _check_can_record(self) -> None:
        reason = support.unsupported_reason()
        if reason:
            raise RecordingError(reason)
        folder = self.settings.output.parent
        try:
            free = self.free_space(folder)
        except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
            raise RecordingError(f"Recordings can't be saved in {folder}: {exc}") from exc
        except OSError as exc:  # some network drives can't say: record, and let Qt report real trouble
            log.warning("Can't read the free space in %s: %s", folder, exc)
            return
        if free < MIN_FREE_TO_START:
            raise RecordingError(f"There isn't room on the drive to record: {free // MB} MB free, and a "
                                 f"recording needs at least {MIN_FREE_TO_START // MB} MB. Free up some "
                                 "space, or record to another folder.")

    def _open(self) -> None:
        s = self.settings
        self._open_sound()
        self._encoder = self._make_encoder(s.output, (s.area[2], s.area[3]), s.output_size, s.fps,
                                           audio=bool(self._captures))
        self._encoder.ready.connect(self._begin)
        self._frames = self._make_screen_frames(s.area, s.fps, self.clock, self._make_overlay(s))
        self._frames.start()
        self._encoder.start()
        # Connected only now: a refusal inside start() is raised above, not also reported by `failed`.
        self._encoder.failed.connect(self._fail)
        self._encoder.finished.connect(self._done)

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

    def _begin(self) -> None:
        if self.state == "starting" and not self._held:
            self.clock.begin(self.now() + START_DELAY)
            self._set_state("recording")

    def _check_started(self) -> None:
        if self.state == "starting" and not self._encoder.is_ready:
            self._fail(f"The video encoder didn't start within {ENCODER_START_TIMEOUT:g} seconds", discard=True)

    # --- recording -------------------------------------------------------------------------------

    def _tick(self) -> None:
        try:
            self._pump_once()
        except Exception as exc:
            log.exception("Recording failed")
            self._fail(f"The recording failed: {exc}")

    def _pump_once(self) -> None:
        frames, encoder = self._frames, self._encoder
        if frames.error is not None and self.state in ACTIVE:
            self._screen_lost(frames.error)
            return
        if self.state in ("recording", "paused") and self._out_of_space():
            return
        if frames.overlay_error is not None and not self._overlay_warned:
            self._overlay_warned = True
            self.warning.emit(f"The pointer can't be drawn, so it's left out: {frames.overlay_error}")
        for index, image in frames.take():
            encoder.add_frame(index, image)
        if self.state in ("recording", "paused") and encoder.has_audio:
            block = self.mixer.pull_ready(self.now())
            if block:
                encoder.add_audio(*block)

    def _out_of_space(self) -> bool:
        """From the latest reading: warn once when the drive runs low; stop when it's nearly full.
        A drive that can't be read is unknown, not full."""
        free = self._space.latest if self._space is not None else None
        if free is None:
            return False
        if free < MIN_FREE_TO_GO_ON:
            message = f"The drive is almost full ({free // MB} MB left), so the recording was stopped and saved"
            log.warning(message)
            self.warning.emit(message)
            self.stop()
            return True
        if free < LOW_SPACE and not self._space_warned:
            self._space_warned = True
            self.warning.emit(f"The drive is running out of space: {free // MB} MB left. The recording stops "
                              f"by itself, and is saved, if it gets below {MIN_FREE_TO_GO_ON // MB} MB.")
        return False

    def _screen_lost(self, error) -> None:
        message = f"The screen stopped being recorded: {error}"
        if self.state == "starting":
            self._fail(message, discard=True)
            return
        log.warning(message)
        self.warning.emit(message)
        self.stop()  # keep what was recorded

    # --- finishing -------------------------------------------------------------------------------

    def _finish_sound(self) -> None:
        if self.state != "finishing":
            return
        try:
            for capture in self._captures:
                capture.stop()
            self._pump_once()  # the last frames
            if self._encoder.has_audio:
                block = self.mixer.pull(self._stop_media)
                if block:
                    self._encoder.add_audio(*block)
            self._encoder.finish()
        except Exception as exc:
            log.exception("Finishing the recording failed")
            self._fail(f"The recording couldn't be finished: {exc}")

    def _check_finished(self) -> None:
        if self.state == "finishing":
            self._fail(f"The recording couldn't be finished within {FINISH_TIMEOUT:g} seconds")

    def _done(self) -> None:
        if self.state != "finishing":
            return
        s = self.settings
        try:
            stats = self.stats()
            self._pump.stop()
            self._close()
            problem, warnings = check_file(s.output, self._stop_media, sound=bool(self._captures),
                                           size=s.output_size, fps=s.fps)
        except Exception as exc:
            log.exception("Checking the recording failed")
            problem, warnings = f"The recording couldn't be checked: {exc}", []
        if problem:
            self._fail(problem)
            return
        for message in warnings:
            self.warning.emit(message)
        self._set_state("finished")
        self.finished.emit(Result(s.output, self._stop_media, stats))

    def _fail(self, message: str, discard: bool = False) -> None:
        if self.state in OVER:
            return
        log.error("Recording failed: %s", message)
        self._end("failed", discard)
        self.failed.emit(message)

    def _end(self, state: str, discard: bool) -> None:
        self._pump.stop()
        self._close(discard)
        self._set_state(state)

    def _close(self, discard: bool = False) -> None:
        """Stop everything (again is fine); `discard` deletes the file once the encoder lets go."""
        if self._space is not None:
            self._space.stop()
        if self._frames is not None:
            self._frames.stop()
            self._frames.take()  # let go of frames still queued
        for capture in self._captures:
            capture.stop()
        if self._encoder is not None:
            self._encoder.close(discard=discard and not self._output_existed)

    def _set_state(self, state: str) -> None:
        self.state = state
        self.state_changed.emit(state)

    def stats(self) -> dict:
        """What happened so far, for the log and for spotting trouble (dropped frames, late sound)."""
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
