"""snipforge/video/recorder.py and support.py: what the computer must have for a recording, and what
happens when it runs out (disk space), checked before starting and while recording."""
import sys
import time

import pytest

from snipforge.video import ffmpeg, support
from snipforge.video import recorder as recorder_module
from snipforge.video.recorder import Recorder, RecordingError, Settings
from tests.test_video_recorder import AREA, FPS, fake_screen_frames, record, sound_maker

pytestmark = pytest.mark.skipif(sys.platform != "win32" and not ffmpeg.available(),
                                reason="Qt's FFmpeg isn't usable here (ffmpeg.unavailable_reason() says why)")
MB = 1024 * 1024


def test_too_little_free_space_stops_the_start(tmp_path, qapp):
    recorder = Recorder(Settings(AREA, tmp_path / "rec.mp4", fps=FPS), screen_frames=fake_screen_frames,
                        capture=sound_maker(), overlay=lambda s: None, free_space=lambda folder: 100 * MB)

    with pytest.raises(RecordingError, match="100 MB free"):
        recorder.start()
    assert recorder.state == "idle" and not (tmp_path / "rec.mp4").exists()


def test_running_low_on_space_warns_once(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr(recorder_module, "DISK_CHECK_SECONDS", 0.1)

    def low(recorder):
        recorder.free_space = lambda folder: 800 * MB

    _recorder, outcome, warnings = record(tmp_path, [(1.0, "stop")], prepare=low)

    assert warnings == ["The drive is running out of space: 800 MB left"]
    assert "result" in outcome


def test_a_nearly_full_drive_stops_the_recording_and_keeps_it(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr(recorder_module, "DISK_CHECK_SECONDS", 0.1)

    def filling(recorder):
        started = time.perf_counter()
        recorder.free_space = lambda folder: (5000 if time.perf_counter() - started < 0.8 else 150) * MB

    _recorder, outcome, warnings = record(tmp_path, [(10.0, "stop")], prepare=filling)

    assert warnings == ["The drive is almost full (150 MB left), so the recording was stopped there"]
    assert 0.3 < outcome["result"].duration < 1.2
    assert ffmpeg.probe(outcome["result"].path).first("video").codec == "h264"


def test_windows_without_a_video_encoder_explains_what_to_add(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr(support, "unsupported_reason", lambda: "No video encoder: add the Media Feature Pack")
    recorder = Recorder(Settings(AREA, tmp_path / "rec.mp4", fps=FPS), screen_frames=fake_screen_frames,
                        capture=sound_maker(), overlay=lambda s: None)

    with pytest.raises(RecordingError, match="Media Feature Pack"):
        recorder.start()


def test_this_windows_has_media_foundation():
    if sys.platform != "win32":
        pytest.skip("Media Foundation is Windows'")
    assert support.unsupported_reason() is None


class WindowsSys:
    platform = "win32"


def test_an_n_edition_is_recognised(monkeypatch):
    monkeypatch.setattr(support, "sys", WindowsSys())
    monkeypatch.setattr(support, "_loads", lambda name: name != "mfplat")

    assert "Media Feature Pack" in support.unsupported_reason()
