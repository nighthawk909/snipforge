"""The video editor's timeline maths (snipforge/video/timeline.py), with the trim range always
starting and ending on a frame, and when each frame starts, read from the file without decoding
(snipforge/video/frame_times.py)."""
import sys

import pytest

from snipforge.video import clip, ffmpeg, frame_times, trim
from snipforge.video.timeline import FrameTimes, TrimRange, format_time

needs_ffmpeg = pytest.mark.skipif(sys.platform != "win32" and not ffmpeg.available(),
                                  reason="Qt's FFmpeg isn't usable here (ffmpeg.unavailable_reason() says why)")


@pytest.fixture
def frames():
    return FrameTimes.uniform(fps=30, count=90)  # 3 s


def test_uniform_frame_times(frames):
    assert len(frames) == 90
    assert frames.end == pytest.approx(3.0)
    assert frames.boundary(0) == 0.0
    assert frames.boundary(15) == pytest.approx(0.5)
    assert frames.boundary(90) == pytest.approx(3.0)  # the end of the last frame


@pytest.mark.parametrize("seconds, index", [(0.0, 0), (0.01, 0), (0.5, 15), (0.52, 15), (2.999, 89), (9.0, 89),
                                            (-1.0, 0)])
def test_the_frame_shown_at_a_time(frames, seconds, index):
    assert frames.index_at(seconds) == index


@pytest.mark.parametrize("seconds, boundary", [(0.0, 0), (0.51, 15), (0.52, 16), (2.99, 90), (5.0, 90), (-2.0, 0)])
def test_the_nearest_frame_boundary(frames, seconds, boundary):
    assert frames.nearest_boundary(seconds) == boundary


def test_frame_times_must_make_sense():
    with pytest.raises(ValueError):
        FrameTimes((), 1.0)
    with pytest.raises(ValueError):
        FrameTimes((0.0, 0.5, 0.4), 1.0)  # not in order
    with pytest.raises(ValueError):
        FrameTimes((0.0, 0.5), 0.5)  # the last frame must last a while


def test_a_new_range_keeps_everything(frames):
    whole = TrimRange.whole(frames)

    assert (whole.first, whole.stop) == (0, 90)
    assert whole.is_whole
    assert (whole.start, whole.end, whole.seconds) == (0.0, pytest.approx(3.0), pytest.approx(3.0))
    assert whole.export_end is None  # "to the end", so trailing sound comes along too


def test_handles_snap_to_frames(frames):
    trimmed = TrimRange.whole(frames).move_in_to(0.51).move_out_to(2.02)

    assert (trimmed.first, trimmed.stop) == (15, 61)
    assert trimmed.start == pytest.approx(0.5)
    assert trimmed.end == pytest.approx(61 / 30)
    assert trimmed.export_end == pytest.approx(61 / 30)
    assert not trimmed.is_whole


def test_the_handles_never_cross_and_keep_at_least_one_frame(frames):
    r = TrimRange.whole(frames).move_out_to(1.0)

    assert r.move_in_to(2.5).first == r.stop - 1
    assert r.move_in_to(2.5).stop == r.stop
    assert r.move_out_to(-1).stop == r.first + 1
    assert TrimRange.whole(frames).move_in_to(99).first == 89


def test_nudging_moves_one_frame_at_a_time(frames):
    r = TrimRange.whole(frames)

    assert r.nudge_in(1).first == 1
    assert r.nudge_in(1).nudge_in(10).first == 11
    assert r.nudge_in(-1).first == 0  # can't go before the start
    assert r.nudge_out(-1).stop == 89
    assert r.nudge_out(5).stop == 90
    assert r.nudge_in(200).first == 89


def test_ranges_are_values_not_shared_state(frames):
    r = TrimRange.whole(frames)
    r.nudge_in(5)

    assert r.first == 0
    with pytest.raises(AttributeError):
        r.first = 3


def test_the_range_knows_which_frames_it_keeps(frames):
    r = TrimRange.whole(frames).with_first(10).with_stop(20)

    assert r.keeps(10) and r.keeps(19)
    assert not r.keeps(9) and not r.keeps(20)
    assert r.last == 19


@pytest.mark.parametrize("seconds, text", [(0, "0:00.00"), (1.5, "0:01.50"), (61.257, "1:01.26"),
                                           (3599.999, "1:00:00.00"), (3725.5, "1:02:05.50")])
def test_times_are_shown_as_minutes_and_seconds(seconds, text):
    assert format_time(seconds) == text


@pytest.mark.parametrize("first, stop", [(5, 5), (6, 5), (-1, 10), (0, 91)])
def test_a_range_that_keeps_nothing_or_too_much_is_refused(frames, first, stop):
    with pytest.raises(ValueError):
        TrimRange(frames, first, stop)


@pytest.fixture(scope="module")
def one_second(tmp_path_factory, qapp):
    return clip.make_test_clip(tmp_path_factory.mktemp("times") / "clip.mp4", seconds=1.0, fps=30)


@needs_ffmpeg
def test_frame_times_are_read_from_a_real_file(one_second):
    times = frame_times.read(one_second)

    assert len(times) == 30
    assert times.starts == pytest.approx([n / 30 for n in range(30)], abs=0.001)
    assert times.end == pytest.approx(1.0, abs=0.001)


@needs_ffmpeg
def test_an_mp4_s_frame_times_come_from_its_index_alone(one_second, monkeypatch):
    """Reading every packet reads the whole file (an hour-long recording, or a OneDrive file not yet
    downloaded); an MP4's index has every frame's time without that."""
    def no_packets(*args):
        raise AssertionError("read the packets")

    monkeypatch.setattr(frame_times, "_packet_times", no_packets)

    assert len(frame_times.read(one_second)) == 30


@needs_ffmpeg
def test_other_containers_are_read_packet_by_packet(tmp_path, qapp):
    """Qt's Matroska starts at about 1 s; times still count from its first frame. (On a busy machine
    Qt's encoder can lose the clip's last frame or so: the times of those there are what's tested.)"""
    path = clip.make_test_clip(tmp_path / "clip.mkv", seconds=1.0, fps=30, container="mkv")

    times = frame_times.read(path)

    assert 28 <= len(times) <= 30
    assert times.starts[0] == 0.0
    assert all(start == pytest.approx(n / 30, abs=0.002) for n, start in enumerate(times.starts))


@needs_ffmpeg
def test_frames_hidden_before_a_trim_s_start_are_left_out(one_second, tmp_path):
    """A trimmed copy carries a few frames before its start (an edit list hides them); they aren't
    part of the video, so they aren't on its timeline."""
    cut = trim.copy(one_second, tmp_path / "cut.mp4", start=0.5)

    times = frame_times.read(cut)

    assert len(times) == 15
    assert times.starts[0] == pytest.approx(0.0, abs=0.001)
    assert times.end == pytest.approx(0.5, abs=0.002)


@needs_ffmpeg
def test_a_file_without_video_is_refused(tmp_path):
    path = tmp_path / "not-a-video.mp4"
    path.write_bytes(b"\x00" * 64)

    with pytest.raises(ffmpeg.FFmpegError):
        frame_times.read(path)

