"""snipforge/video/recorder.py: how recordings end. Cancelled, finished there and then (Windows going
to sleep), and every way finishing can go wrong, which must end in `failed` rather than a recording
stuck "finishing" for ever."""
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from snipforge.video import ffmpeg
from snipforge.video import recorder as recorder_module
from snipforge.video.encoder import Encoder
from snipforge.video.recorder import Recorder, RecordingError, Settings
from snipforge.video.screen_frames import ScreenFrames
from tests.test_video_recorder import AREA, FPS, FakeSound, Stamper, StillScreen, gone, grey, record, sound_maker

pytestmark = pytest.mark.skipif(sys.platform != "win32" and not ffmpeg.available(),
                                reason="Qt's FFmpeg isn't usable here (ffmpeg.unavailable_reason() says why)")


def test_cancelling_a_recording_keeps_nothing(tmp_path, qapp):
    recorder, outcome, _ = record(tmp_path, [(0.6, "cancel")])

    assert outcome.get("cancelled") and "result" not in outcome and "failure" not in outcome
    assert gone(qapp, tmp_path / "rec.mp4")


def test_finish_now_finishes_the_file_there_and_then(tmp_path, qapp):
    said = {}

    def finish(recorder):
        said["finished"] = recorder.finish_now(10)
        said["state"] = recorder.state

    _recorder, outcome, _ = record(tmp_path, [(0.8, finish)])

    assert said == {"finished": True, "state": "finished"}  # before finish_now returned
    assert ffmpeg.probe(outcome["result"].path).first("video").codec == "h264"


def test_a_failure_while_finishing_is_reported_not_left_hanging(tmp_path, qapp):
    def broken_mix(recorder):
        def pull(until):
            raise RuntimeError("the mixer broke")
        recorder.mixer.pull = pull

    recorder, outcome, _ = record(tmp_path, [(0.6, "stop")], prepare=broken_mix)

    assert "the mixer broke" in outcome["failure"] and recorder.state == "failed"


class NeverFinishes(Encoder):
    def finish(self):
        pass  # Qt never closes the file


def test_an_encoder_that_never_finishes_fails_after_the_time_limit(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr(recorder_module, "FINISH_TIMEOUT", 0.6)

    recorder, outcome, _ = record(tmp_path, [(0.6, "stop")], encoder=NeverFinishes)

    assert "couldn't be finished within" in outcome["failure"]


class DyingScreen(StillScreen):
    """A screen (monitor, graphics driver) that goes away 0.6 s into the recording (however long
    the encoder took to start before that)."""

    def __init__(self, clock):
        self.clock = clock

    def grab(self, timeout_ms):
        if (self.clock.media_time(time.perf_counter()) or 0) > 0.6:
            raise OSError("The display went away")
        super().grab(timeout_ms)


def test_losing_the_screen_mid_recording_keeps_what_was_recorded(tmp_path, qapp):
    def dying(area, fps, clock, _overlay):
        return ScreenFrames(area, fps, clock, Stamper(clock), open_screen=lambda a: DyingScreen(clock),
                            first_image=grey)

    _recorder, outcome, warnings = record(tmp_path, [(5.0, "stop")], screen_frames=dying)

    assert warnings == ["The screen stopped being recorded: The display went away"]
    assert 0.55 < outcome["result"].duration < 2.0  # kept up to when the screen went (noticed within a beat)
    assert ffmpeg.probe(outcome["result"].path).first("video").codec == "h264"


def test_a_pointer_that_cant_be_drawn_is_left_out_and_the_recording_goes_on(tmp_path, qapp):
    class BrokenPointer:
        def draw(self, frame, now):
            raise ValueError("no pointer shape")

    def frames(area, fps, clock, _overlay):
        return ScreenFrames(area, fps, clock, BrokenPointer(), open_screen=lambda a: StillScreen(), first_image=grey)

    _recorder, outcome, warnings = record(tmp_path, [(0.8, "stop")], screen_frames=frames)

    assert warnings == ["The pointer can't be drawn, so it's left out: no pointer shape"]
    assert "result" in outcome


def test_a_refusal_at_start_is_raised_once_and_closes_the_sound(tmp_path, qapp):
    made = []

    def capture(kind, device_id, on_packet, on_error):
        made.append(FakeSound(kind, device_id, on_packet, on_error))
        return made[-1]

    recorder = Recorder(Settings(AREA, tmp_path / "no such folder" / "rec.mp4", fps=FPS), capture=capture,
                        screen_frames=lambda area, fps, clock, o: ScreenFrames(
                            area, fps, clock, Stamper(clock), open_screen=lambda a: StillScreen(), first_image=grey),
                        overlay=lambda s: None, free_space=lambda folder: 10**10)  # Qt refuses, not the space check
    failures = []
    recorder.failed.connect(failures.append)

    with pytest.raises(RecordingError):
        recorder.start()
    loop = QEventLoop()
    QTimer.singleShot(300, loop.quit)  # anything queued would arrive now
    loop.exec()
    assert failures == [] and len(made) == 2
    assert all(sound.stop_event.is_set() for sound in made)


def test_a_file_that_was_already_there_is_never_deleted(tmp_path, qapp):
    existing = tmp_path / "rec.mp4"
    existing.write_bytes(b"someone's video")

    def refuse(area, fps, clock, overlay):
        def no(a):
            raise OSError("no screen")
        return ScreenFrames(area, fps, clock, overlay, open_screen=no, first_image=grey)

    recorder = Recorder(Settings(AREA, existing, fps=FPS), screen_frames=refuse, capture=sound_maker(),
                        overlay=lambda s: None, free_space=lambda folder: 10**12)
    with pytest.raises(RecordingError):
        recorder.start()
    loop = QEventLoop()
    QTimer.singleShot(500, loop.quit)
    loop.exec()
    assert existing.exists()


@pytest.mark.parametrize("change, reason", [
    ({"area": (0, 0, 40, 300)}, "too small"),
    ({"area": (0, 0, 3000, 60)}, "too small or thin"),  # 1920 x 38 once scaled to fit 1080p
    ({"area": (0, 0, 320.0, 240)}, "whole numbers"),
    ({"fps": 0}, "1 to 60"),
    ({"fps": 61}, "1 to 60"),
    ({"fps": True}, "1 to 60"),
    ({"max_size": (10, 10)}, "largest size"),
])
def test_settings_that_cant_be_recorded_are_refused(change, reason):
    values = {"area": AREA, "output": Path("rec.mp4"), **change}

    with pytest.raises(ValueError, match=reason):
        Settings(**values)


def test_a_camera_picture_that_cant_be_reached_is_left_out_with_a_warning(tmp_path, qapp):
    from snipforge.video import recorder as recorder_module

    def frames(area, fps, clock, overlay):
        return ScreenFrames(area, fps, clock, overlay, open_screen=lambda a: StillScreen(), first_image=grey)

    recorder = Recorder(Settings(AREA, tmp_path / "rec.mp4", fps=FPS, camera_share="sfcam-" + "c" * 16,
                                 camera_bubble=(0, 0, 100, 100)),
                        screen_frames=frames, capture=sound_maker(), free_space=lambda folder: 10**12,
                        overlay=lambda s: recorder_module._real_overlay(s))  # SnipForge's share isn't there
    warnings, outcome = [], {}
    recorder.warning.connect(warnings.append)
    loop = QEventLoop()
    recorder.finished.connect(lambda r: (outcome.update(result=r), loop.quit()))
    recorder.failed.connect(lambda m: (outcome.update(failure=m), loop.quit()))
    recorder.state_changed.connect(lambda state: state == "recording" and QTimer.singleShot(600, recorder.stop))
    recorder.start()
    QTimer.singleShot(20_000, loop.quit)
    loop.exec()

    assert warnings[0].startswith("The camera can't be recorded, so it's left out") and "result" in outcome


def test_a_camera_that_cant_be_drawn_is_left_out_and_the_pointer_stays(tmp_path, qapp):
    from snipforge.video.camera_share import BubbleState
    from snipforge.video.webcam_layer import CompositeOverlay, WebcamLayer

    class BrokenCamera:
        def latest(self):
            raise ValueError("no picture format")

    drawn = []

    class Pointer:
        def draw(self, frame, now):
            drawn.append(now)

    def frames(area, fps, clock, _overlay):
        camera = WebcamLayer(area, BrokenCamera(), BubbleState((0, 0, 50, 50), "circle"))
        return ScreenFrames(area, fps, clock, CompositeOverlay(camera, Pointer()),
                            open_screen=lambda a: StillScreen(), first_image=grey)

    _recorder, outcome, warnings = record(tmp_path, [(0.8, "stop")], screen_frames=frames)

    assert warnings == ["The camera can't be drawn, so it's left out: no picture format"]
    assert "result" in outcome and len(drawn) > 10  # the pointer went on being drawn
