"""snipforge/video/recording_check.py: is a finished file what was recorded?"""
import sys

import pytest

from snipforge.video import clip, ffmpeg
from snipforge.video.recording_check import check_file
from tests.test_video_encoder import record

pytestmark = pytest.mark.skipif(sys.platform != "win32" and not ffmpeg.available(),
                                reason="Qt's FFmpeg isn't usable here (ffmpeg.unavailable_reason() says why)")


@pytest.fixture(scope="module")
def second(tmp_path_factory, qapp):
    """A 1 s, 320x240, 30 fps clip with sound, like a short recording."""
    return clip.make_test_clip(tmp_path_factory.mktemp("check") / "one.mp4", seconds=1.0)


def test_a_file_that_is_the_recording_passes(second):
    assert check_file(second, 1.0, sound=True, size=(320, 240), fps=30) == ("", [])


def test_a_file_much_shorter_than_the_recording_fails(second):
    problem, _ = check_file(second, 2.0, sound=True, size=(320, 240), fps=30)

    assert "picture is" in problem and "not the 2.0 s recorded" in problem


def test_a_picture_of_the_wrong_size_fails(second):
    problem, _ = check_file(second, 1.0, sound=True, size=(640, 480), fps=30)

    assert problem == "The recording is 320x240, not 640x480"


def test_missing_sound_fails(tmp_path, qapp):
    silent = tmp_path / "silent.mp4"
    record(silent, 30, audio=False)  # 1 s of video, no sound

    problem, _ = check_file(silent, 1.0, sound=True, size=(320, 240), fps=30)

    assert problem == "The recording was saved without its sound"
    assert check_file(silent, 1.0, sound=False, size=(320, 240), fps=30) == ("", [])


def test_too_few_frames_is_a_warning_not_a_failure(second):
    problem, warnings = check_file(second, 1.0, sound=True, size=(320, 240), fps=60)

    assert problem == "" and warnings == []  # 30 of 60 is the limit, not under it
    problem, warnings = check_file(second, 1.0, sound=True, size=(320, 240), fps=120)
    assert problem == "" and len(warnings) == 1 and "30 of 120 frames" in warnings[0]


def test_a_file_that_isnt_a_video_fails(tmp_path):
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video")

    problem, _ = check_file(junk, 1.0, sound=False, size=(320, 240), fps=30)

    assert problem.startswith("The recording was saved but can't be read back")
