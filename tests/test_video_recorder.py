"""snipforge/video/recorder.py: a whole recording, with a fake screen and fake sound sources (the
desktop suite records the real ones). The fake screen's frames show the frame number due at the
moment they were made, so the recording's timing is checked frame by frame."""
import array
import math
import sys
import threading
import time

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QColor, QImage, QPainter

from snipforge.video import clip, ffmpeg, frames
from snipforge.video import recorder as recorder_module
from snipforge.video.encoder import Encoder
from snipforge.video.recorder import START_DELAY, Recorder, RecordingError, Settings
from snipforge.video.screen_frames import LATE_FRAMES, ScreenFrames

pytestmark = pytest.mark.skipif(sys.platform != "win32" and not ffmpeg.available(),
                                reason="Qt's FFmpeg isn't usable here (ffmpeg.unavailable_reason() says why)")
FPS = 30
AREA = (0, 0, 320, 240)
ROOMY = 10**12  # bytes free: the tests' drives never run low unless a test says so
STAMP_SLACK = 0.05  # frame times are whole microseconds: a frame 4 late can measure 4.00001
# How close the file's length must be to the time recorded. On an overloaded machine (off Windows,
# where recording isn't shipped) the file has come out ~5 frames longer: issue #107.
FILE_LENGTH_SLACK = 0.1 if sys.platform == "win32" else 0.25
BUSY = "The computer was too busy"  # the recorder's own warning when most frames couldn't be made


class StillScreen:
    def grab(self, timeout_ms):
        time.sleep(timeout_ms / 1000)

    def close(self):
        pass


def grey(_area):
    image = QImage(320, 240, QImage.Format.Format_RGB32)
    image.fill(QColor(60, 60, 60))
    return image


class Stamper:
    """Plays the pointer overlay's part: draws, as blocks, the frame number due when the frame is made."""

    def __init__(self, clock):
        self.clock, self.due = clock, 0

    def draw(self, frame, now):
        media = self.clock.media_time(now)  # None if a pause landed since the frame thread looked
        due = self.due = round(media * FPS) if media is not None else self.due
        painter = QPainter(frame)
        for bit in range(clip.NUMBER_BITS):
            painter.fillRect(clip._block(bit, frame.width(), frame.height()),
                             QColor("white") if due >> bit & 1 else QColor("black"))
        painter.end()


def fake_screen_frames(area, fps, clock, _overlay):
    return ScreenFrames(area, fps, clock, Stamper(clock), open_screen=lambda a: StillScreen(), first_image=grey)


class FakeSound:
    """A sound card: 10 ms packets of a tone, stamped with the time they were 'captured'."""

    def __init__(self, kind, device_id, on_packet, on_error, fail_to_start=False, lose_after=None):
        self.kind, self.on_packet, self.on_error = kind, on_packet, on_error
        self.fail_to_start, self.lose_after = fail_to_start, lose_after
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        if self.fail_to_start:
            raise RuntimeError(f"No {self.kind} here")
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        sent, started = 0, time.perf_counter()
        hz = 440 if self.kind == "microphone" else 660
        while not self.stop_event.is_set():
            due = started + sent * 480 / 48000
            if time.perf_counter() < due + 0.01:
                time.sleep(0.002)
                continue
            if self.lose_after is not None and time.perf_counter() - started > self.lose_after:
                self.on_error(RuntimeError(f"The {self.kind} was unplugged"))
                return
            samples = array.array("f", (0.1 * math.sin(2 * math.pi * hz * (sent * 480 + i) / 48000)
                                        for i in range(480) for _ in (0, 1)))
            self.on_packet(samples.tobytes(), 480, due, False)
            sent += 1

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(2)


class SlowToStart(Encoder):
    """Qt's encoder on a busy machine: for a while after it starts, it takes no frames."""

    WARM_UP_MS = 800

    def start(self):
        self.warm = False
        super().start()
        QTimer.singleShot(self.WARM_UP_MS, self, self._warmed_up)

    def _warmed_up(self):
        self.warm = True
        self._input_ready("video")
        self._send_video()

    def _input_ready(self, which):
        if which != "video" or getattr(self, "warm", True):
            super()._input_ready(which)

    def _send_video(self):
        if getattr(self, "warm", True):
            super()._send_video()


class NeverStarts(SlowToStart):
    WARM_UP_MS = 60_000


def sound_maker(**behaviour):
    def make(kind, device_id, on_packet, on_error):
        return FakeSound(kind, device_id, on_packet, on_error, **behaviour.get(kind, {}))
    return make


def record(tmp_path, steps, *, sound=None, encoder=None, screen_frames=None, prepare=None, hold=False,
           from_recording=True, **settings):
    """Run a recording: `steps` is a list of (seconds to wait, action) ending the recording, an action
    being a Recorder method's name or a function given the recorder. The waits count from when the
    recording begins (its encoder warmed up: up to a second or more on a busy machine), or with
    from_recording=False from start(). outcome["recording_at"] is when it began (perf_counter), and
    outcome["at"][action] is (the recording's time just before, perf_counter before reading it,
    perf_counter after the action returned): where the action really happened is between those, on
    any machine, however late its timer came."""
    recorder = Recorder(Settings(AREA, tmp_path / "rec.mp4", fps=FPS, **settings),
                        screen_frames=screen_frames or fake_screen_frames, capture=sound or sound_maker(),
                        overlay=lambda s: None, encoder=encoder, free_space=lambda folder: ROOMY)
    if prepare:
        prepare(recorder)
    outcome, warnings = {"at": {}, "states": []}, []
    loop = QEventLoop()
    recorder.warning.connect(warnings.append)
    recorder.state_changed.connect(outcome["states"].append)
    recorder.finished.connect(lambda result: (outcome.update(result=result), loop.quit()))
    recorder.failed.connect(lambda message: (outcome.update(failure=message), loop.quit()))
    recorder.cancelled.connect(lambda: (outcome.update(cancelled=True), loop.quit()))

    def run(action):
        if callable(action):
            action(recorder)
            return
        before = time.perf_counter()
        media = recorder.media_time()
        getattr(recorder, action)()
        outcome["at"][action] = (media, before, time.perf_counter())

    def schedule():
        at = 0.0
        for seconds, action in steps:
            at += seconds
            QTimer.singleShot(int(at * 1000), lambda action=action: run(action))
        QTimer.singleShot(int(at * 1000) + 30_000, loop.quit)

    def began(state):
        if state == "recording" and "recording_at" not in outcome:
            outcome["recording_at"] = time.perf_counter()
            if from_recording:
                schedule()

    recorder.state_changed.connect(began)
    recorder.start(hold=hold)
    if from_recording:
        QTimer.singleShot(60_000, loop.quit)  # never began: give up
    else:
        schedule()
    loop.exec()
    return recorder, outcome, warnings


def acted(outcome, action) -> tuple[float, float]:
    """The earliest and latest the recording's time can have been when `action` ran."""
    media, before, after = outcome["at"][action]
    return media, media + (after - before)


def frame_times(path):
    """(due frame number shown, the frame's own time in frames) for every frame of the file."""
    return [(clip.frame_number(image), t * FPS) for t, image in frames.read(path)]


def assert_in_time(shown, result):
    """Each frame shows the number due when it was made, so it should be at its own place in the
    video, never before it. One can be up to LATE_FRAMES whole frames late before the thread skips ahead, and
    the Stamper rounds to the nearest frame where the frame thread counts whole ones: so up to
    LATE_FRAMES + 1 (3.6 frames late is kept, and shows 4), on any machine. A jump (a pause making
    frames, a lost stretch) is many more. Not the first frame, made at the frame thread's first wake
    however late, and never skipped past. How late they are on average is the machine's doing (on
    a busy CI runner most can be a frame or two late), so it isn't checked; frames' exact places
    are, in test_video_encoder.py. And most of them are there, apart from those the frame thread
    knowingly skipped (frames the encoder lost aren't excused)."""
    late = [number - at for number, at in shown[1:]]
    assert all(-STAMP_SLACK <= frames <= LATE_FRAMES + 1 + STAMP_SLACK for frames in late), sorted(late)
    skipped = result.stats["frames_skipped_late"]
    assert len(shown) >= 0.6 * result.duration * FPS - skipped, (len(shown), skipped)


def test_a_recording_has_its_picture_and_both_sounds_at_the_right_times(tmp_path, qapp):
    recorder, outcome, warnings = record(tmp_path, [(1.5, "stop")])

    result = outcome["result"]
    info = ffmpeg.probe(result.path)
    assert (info.first("video").codec, info.first("audio").codec) == ("h264", "aac")
    earliest, latest = acted(outcome, "stop")
    assert earliest <= result.duration <= latest  # it ends where stop was asked for
    _media, asked, _after = outcome["at"]["stop"]
    assert result.duration >= asked - outcome["recording_at"] - START_DELAY  # from when it began, the warm-up left out
    assert info.duration == pytest.approx(result.duration, abs=FILE_LENGTH_SLACK)
    assert_in_time(frame_times(result.path), result)
    # An overloaded machine is rightly told so; nothing else may be wrong.
    assert [w for w in warnings if not w.startswith(BUSY)] == [] and recorder.state == "finished"
    assert result.stats["sound"]["microphone"]["placed_s"] > 0.6 * result.duration  # sound for most of it


def test_time_zero_waits_for_an_encoder_slow_to_start(tmp_path, qapp):
    before = time.perf_counter()
    recorder, outcome, _ = record(tmp_path, [(1.5, "stop")], encoder=SlowToStart, from_recording=False)

    result = outcome["result"]
    assert outcome["states"][:2] == ["starting", "recording"]
    earliest, latest = acted(outcome, "stop")
    assert earliest <= result.duration <= latest
    assert result.duration <= outcome["at"]["stop"][2] - before - SlowToStart.WARM_UP_MS / 1000  # no warm-up
    assert_in_time(frame_times(result.path), result)  # none lost while the encoder warmed up


def test_a_held_start_begins_only_when_told_to_go(tmp_path, qapp):
    # The recording UI opens everything while its countdown runs, and begins as it ends.
    recorder, outcome, _ = record(tmp_path, [(0.8, "go"), (0.6, "stop")], hold=True, from_recording=False)

    result = outcome["result"]
    assert outcome["at"]["go"][0] == 0.0  # nothing recorded while held
    assert outcome["states"][:2] == ["starting", "recording"]
    earliest, latest = acted(outcome, "stop")
    assert earliest <= result.duration <= latest
    went, stopped = outcome["at"]["go"][1], outcome["at"]["stop"][2]
    assert result.duration <= stopped - went - START_DELAY  # counted from go, not from start: the hold isn't in it
    assert_in_time(frame_times(result.path), result)


def test_go_before_the_encoder_is_ready_begins_once_it_is(tmp_path, qapp):
    before = time.perf_counter()  # the encoder starts warming up after this
    recorder, outcome, _ = record(tmp_path, [(0.1, "go"), (1.5, "stop")], hold=True, encoder=SlowToStart,
                                  from_recording=False)

    assert outcome["states"][:2] == ["starting", "recording"]
    # Begun once warm, not at go: at least WARM_UP_MS after the start (measured, as a busy machine's
    # timers come late). Begun at go, it'd be ~0.7 s longer.
    stopped = outcome["at"]["stop"][2]  # after stop returned
    assert outcome["result"].duration <= stopped - before - SlowToStart.WARM_UP_MS / 1000


def test_an_encoder_that_never_starts_fails_the_recording(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr(recorder_module, "ENCODER_START_TIMEOUT", 0.5)

    recorder, outcome, _ = record(tmp_path, [(3.0, "stop")], encoder=NeverStarts, from_recording=False)

    assert "didn't start" in outcome["failure"]
    assert recorder.state == "failed" and "recording" not in outcome["states"]


def gone(qapp, path, seconds=5.0) -> bool:
    """Whether `path` is deleted within `seconds` (Qt lets go of a file a moment after stopping)."""
    deadline = time.perf_counter() + seconds
    while path.exists() and time.perf_counter() < deadline:
        qapp.processEvents()
        time.sleep(0.05)
    return not path.exists()


def test_stopping_before_the_encoder_is_ready_cancels_and_leaves_no_file(tmp_path, qapp):
    recorder, outcome, _ = record(tmp_path, [(0.3, "stop")], encoder=NeverStarts, from_recording=False)

    assert outcome.get("cancelled") and "failure" not in outcome
    assert recorder.state == "cancelled"
    assert gone(qapp, tmp_path / "rec.mp4")


def test_a_pause_is_left_out_of_the_recording(tmp_path, qapp):
    recorder, outcome, _ = record(tmp_path, [(0.7, "pause"), (0.8, "resume"), (0.7, "stop")])

    result = outcome["result"]
    pause_low, pause_high = acted(outcome, "pause")
    _m, resume_asked, resume_done = outcome["at"]["resume"]
    _m, stop_asked, stop_done = outcome["at"]["stop"]
    assert pause_low + stop_asked - resume_done <= result.duration <= pause_high + stop_done - resume_asked
    assert ffmpeg.probe(result.path).duration == pytest.approx(result.duration, abs=FILE_LENGTH_SLACK)
    assert_in_time(frame_times(result.path), result)  # no jump where the pause was


def test_stopping_while_paused_ends_at_the_pause(tmp_path, qapp):
    _recorder, outcome, _ = record(tmp_path, [(0.8, "pause"), (0.6, "stop")])

    earliest, latest = acted(outcome, "pause")
    assert earliest <= outcome["result"].duration <= latest


def test_a_microphone_that_cant_be_opened_warns_and_records_without_it(tmp_path, qapp):
    recorder, outcome, warnings = record(tmp_path, [(1.0, "stop")],
                                         sound=sound_maker(microphone={"fail_to_start": True}))

    assert warnings == ["No microphone here"]
    assert ffmpeg.probe(outcome["result"].path).first("audio").codec == "aac"  # the system sound
    assert "microphone" in outcome["result"].stats["sound"]


def test_a_recording_without_any_sound(tmp_path, qapp):
    _recorder, outcome, _ = record(tmp_path, [(0.8, "stop")], microphone=False, system_audio=False)

    info = ffmpeg.probe(outcome["result"].path)
    assert info.first("audio") is None and info.first("video").codec == "h264"


def test_a_sound_source_lost_while_recording_warns_and_the_recording_goes_on(tmp_path, qapp):
    _recorder, outcome, warnings = record(tmp_path, [(1.2, "stop")],
                                          sound=sound_maker(system={"lose_after": 0.4}))

    assert warnings == ["The system was unplugged"]
    earliest, latest = acted(outcome, "stop")
    assert earliest <= outcome["result"].duration <= latest


def test_a_screen_that_cant_be_recorded_stops_it_starting(tmp_path, qapp):
    def no_screen(area, fps, clock, overlay):
        def refuse(a):
            raise OSError("Windows won't share the screen")
        return ScreenFrames(area, fps, clock, overlay, open_screen=refuse, first_image=grey)

    recorder = Recorder(Settings(AREA, tmp_path / "rec.mp4", fps=FPS), screen_frames=no_screen,
                        capture=sound_maker(), overlay=lambda s: None, free_space=lambda folder: ROOMY)

    with pytest.raises(RecordingError, match="won't share the screen"):
        recorder.start()
    assert not (tmp_path / "rec.mp4").exists()


def test_big_areas_are_recorded_at_most_1080p(tmp_path, qapp):
    recorder = Recorder(Settings((0, 0, 2560, 1600), tmp_path / "big.mp4", fps=FPS), capture=sound_maker(),
                        screen_frames=lambda area, fps, clock, _o: ScreenFrames(
                            area, fps, clock, Stamper(clock), open_screen=lambda a: StillScreen(),
                            first_image=lambda a: QImage(2560, 1600, QImage.Format.Format_RGB32)),
                        overlay=lambda s: None, free_space=lambda folder: ROOMY)
    loop = QEventLoop()
    recorder.finished.connect(lambda r: loop.quit())
    recorder.failed.connect(lambda m: loop.quit())
    recorder.state_changed.connect(lambda state: state == "recording" and QTimer.singleShot(500, recorder.stop))
    recorder.start()
    QTimer.singleShot(20_000, loop.quit)
    loop.exec()

    video = ffmpeg.probe(tmp_path / "big.mp4").first("video")
    assert (video.width, video.height) == (1728, 1080)
