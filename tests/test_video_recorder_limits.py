"""snipforge/video/recorder.py: what a recording needs from the computer before it starts (support.py)
and the drive while it runs (disk_space.py)."""
import sys
import time
import weakref

import pytest

from snipforge.video import ffmpeg, support
from snipforge.video import recorder as recorder_module
from snipforge.video.disk_space import MB
from snipforge.video.recorder import Recorder, RecordingError, Settings
from tests.test_video_recorder import AREA, FPS, fake_screen_frames, record, sound_maker

pytestmark = pytest.mark.skipif(sys.platform != "win32" and not ffmpeg.available(),
                                reason="Qt's FFmpeg isn't usable here (ffmpeg.unavailable_reason() says why)")


def recorder_with(tmp_path, free_space, name="rec.mp4"):
    return Recorder(Settings(AREA, tmp_path / name, fps=FPS), screen_frames=fake_screen_frames,
                    capture=sound_maker(), overlay=lambda s: None, free_space=free_space)


def test_too_little_free_space_stops_the_start(tmp_path, qapp):
    recorder = recorder_with(tmp_path, lambda folder: 100 * MB)

    with pytest.raises(RecordingError, match="100 MB free.*Free up some space"):
        recorder.start()
    assert recorder.state == "idle" and not (tmp_path / "rec.mp4").exists()


def test_a_folder_that_isnt_there_stops_the_start(tmp_path, qapp):
    def missing(folder):
        raise FileNotFoundError(2, "The system cannot find the path specified")

    with pytest.raises(RecordingError, match="can't be saved in"):
        recorder_with(tmp_path, missing).start()


def test_a_drive_that_cant_say_how_full_it_is_still_records(tmp_path, qapp):
    def cant_say(folder):
        raise OSError(50, "The request is not supported")

    _recorder, outcome, warnings = record(tmp_path, [(0.6, "stop")],
                                          prepare=lambda r: setattr(r, "free_space", cant_say))

    assert "result" in outcome and warnings == []


def test_running_low_on_space_warns_once(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr(recorder_module, "DISK_CHECK_SECONDS", 0.05)

    _recorder, outcome, warnings = record(tmp_path, [(1.0, "stop")],
                                          prepare=lambda r: setattr(r, "free_space", lambda folder: 800 * MB))

    assert warnings == ["The drive is running out of space: 800 MB left. The recording stops by itself, "
                        "and is saved, if it gets below 200 MB."]
    assert "result" in outcome


def test_a_nearly_full_drive_stops_the_recording_and_keeps_it(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr(recorder_module, "DISK_CHECK_SECONDS", 0.05)

    def filling(recorder):  # full half a second into the recording itself, however long it took to start
        recording = weakref.ref(recorder)  # not the recorder itself: no cycle for the collector to free
        recorder.free_space = lambda folder: (150 if recording().media_time() > 0.5 else 5000) * MB

    _recorder, outcome, warnings = record(tmp_path, [(10.0, "stop")], prepare=filling)

    assert warnings == ["The drive is almost full (150 MB left), so the recording was stopped and saved"]
    assert 0.4 < outcome["result"].duration < 3.0  # stopped soon after it filled, not at 10 s
    assert ffmpeg.probe(outcome["result"].path).first("video").codec == "h264"


def test_a_drive_that_cant_be_read_mid_recording_doesnt_end_it(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr(recorder_module, "DISK_CHECK_SECONDS", 0.05)

    def unplugged_and_back(recorder):
        recording = weakref.ref(recorder)

        def read(folder):
            if 0.2 < recording().media_time() < 0.6:
                raise OSError(21, "The device is not ready")
            return 5000 * MB
        recorder.free_space = read

    _recorder, outcome, warnings = record(tmp_path, [(1.0, "stop")], prepare=unplugged_and_back)

    assert "result" in outcome and warnings == []


def test_the_drive_is_read_on_its_own_thread_every_few_seconds(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr(recorder_module, "DISK_CHECK_SECONDS", 0.25)
    reads = []

    def counted(recorder):
        recorder.free_space = lambda folder: reads.append(folder) or 5000 * MB

    before = time.perf_counter()
    record(tmp_path, [(1.0, "stop")], prepare=counted)
    took = time.perf_counter() - before

    assert 2 <= len(reads) <= took / 0.25 + 2  # one before starting, then every 0.25 s; not every frame


def test_windows_without_a_video_encoder_explains_what_to_add(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr(support, "unsupported_reason", lambda: "No video encoder: add the Media Feature Pack")

    with pytest.raises(RecordingError, match="Media Feature Pack"):
        recorder_with(tmp_path, lambda folder: 5000 * MB).start()
