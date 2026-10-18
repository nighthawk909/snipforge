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
from snipforge.video.recorder import Recorder, RecordingError, Settings
from snipforge.video.screen_frames import ScreenFrames

pytestmark = pytest.mark.skipif(sys.platform != "win32" and not ffmpeg.available(),
                                reason="Qt's FFmpeg isn't usable here (ffmpeg.unavailable_reason() says why)")
FPS = 30
AREA = (0, 0, 320, 240)


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
        self.clock = clock

    def draw(self, frame, now):
        due = round((self.clock.media_time(now) or 0) * FPS)
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


def sound_maker(**behaviour):
    def make(kind, device_id, on_packet, on_error):
        return FakeSound(kind, device_id, on_packet, on_error, **behaviour.get(kind, {}))
    return make


def record(tmp_path, steps, *, sound=None, **settings):
    """Run a recording: `steps` is a list of (seconds to wait, action on the recorder) ending in stop."""
    recorder = Recorder(Settings(AREA, tmp_path / "rec.mp4", fps=FPS, **settings),
                        screen_frames=fake_screen_frames, capture=sound or sound_maker(),
                        overlay=lambda s: None)
    outcome, warnings = {}, []
    loop = QEventLoop()
    recorder.warning.connect(warnings.append)
    recorder.finished.connect(lambda result: (outcome.update(result=result), loop.quit()))
    recorder.failed.connect(lambda message: (outcome.update(failure=message), loop.quit()))
    recorder.start()
    at = 0.0
    for seconds, action in steps:
        at += seconds
        QTimer.singleShot(int(at * 1000), getattr(recorder, action))
    QTimer.singleShot(int(at * 1000) + 30_000, loop.quit)
    loop.exec()
    return recorder, outcome, warnings


def frame_times(path):
    """(due frame number shown, the frame's own time in frames) for every frame of the file."""
    return [(clip.frame_number(image), t * FPS) for t, image in frames.read(path)]


def test_a_recording_has_its_picture_and_both_sounds_at_the_right_times(tmp_path, qapp):
    recorder, outcome, warnings = record(tmp_path, [(1.5, "stop")])

    result = outcome["result"]
    info = ffmpeg.probe(result.path)
    assert (info.first("video").codec, info.first("audio").codec) == ("h264", "aac")
    assert result.duration == pytest.approx(1.5, abs=0.15)
    assert info.duration == pytest.approx(result.duration, abs=0.1)
    shown = frame_times(result.path)
    assert len(shown) >= 0.9 * result.duration * FPS
    assert all(abs(number - at) <= 1.5 for number, at in shown)  # each frame at its own time
    assert warnings == [] and recorder.state == "finished"
    assert result.stats["sound"]["microphone"]["placed_s"] > 1.0


def test_a_pause_is_left_out_of_the_recording(tmp_path, qapp):
    recorder, outcome, _ = record(tmp_path, [(0.7, "pause"), (0.8, "resume"), (0.7, "stop")])

    result = outcome["result"]
    assert result.duration == pytest.approx(1.4, abs=0.2)
    assert ffmpeg.probe(result.path).duration == pytest.approx(result.duration, abs=0.1)
    shown = frame_times(result.path)
    assert all(abs(number - at) <= 1.5 for number, at in shown)  # no jump where the pause was


def test_stopping_while_paused_ends_at_the_pause(tmp_path, qapp):
    _recorder, outcome, _ = record(tmp_path, [(0.8, "pause"), (0.6, "stop")])

    assert outcome["result"].duration == pytest.approx(0.8, abs=0.15)


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
    assert outcome["result"].duration == pytest.approx(1.2, abs=0.15)


def test_a_screen_that_cant_be_recorded_stops_it_starting(tmp_path, qapp):
    def no_screen(area, fps, clock, overlay):
        def refuse(a):
            raise OSError("Windows won't share the screen")
        return ScreenFrames(area, fps, clock, overlay, open_screen=refuse, first_image=grey)

    recorder = Recorder(Settings(AREA, tmp_path / "rec.mp4", fps=FPS), screen_frames=no_screen,
                        capture=sound_maker(), overlay=lambda s: None)

    with pytest.raises(RecordingError, match="won't share the screen"):
        recorder.start()
    assert not (tmp_path / "rec.mp4").exists()


def test_big_areas_are_recorded_at_most_1080p(tmp_path, qapp):
    recorder = Recorder(Settings((0, 0, 2560, 1600), tmp_path / "big.mp4", fps=FPS), capture=sound_maker(),
                        screen_frames=lambda area, fps, clock, _o: ScreenFrames(
                            area, fps, clock, Stamper(clock), open_screen=lambda a: StillScreen(),
                            first_image=lambda a: QImage(2560, 1600, QImage.Format.Format_RGB32)),
                        overlay=lambda s: None)
    loop = QEventLoop()
    recorder.finished.connect(lambda r: loop.quit())
    recorder.failed.connect(lambda m: loop.quit())
    recorder.start()
    QTimer.singleShot(500, recorder.stop)
    QTimer.singleShot(20_000, loop.quit)
    loop.exec()

    video = ffmpeg.probe(tmp_path / "big.mp4").first("video")
    assert (video.width, video.height) == (1728, 1080)
