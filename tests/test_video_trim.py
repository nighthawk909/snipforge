"""Trimming and copying videos without re-encoding (snipforge/video/trim.py), and reading their
frames (snipforge/video/frames.py), with the FFmpeg libraries Qt ships. Test clips carry each
frame's number as blocks, so "the right frame" is checked exactly."""
import shutil

import pytest

from snipforge.video import clip, ffmpeg, frames, trim

FPS = 30


@pytest.fixture(scope="module")
def two_seconds(tmp_path_factory, qapp):
    return clip.make_test_clip(tmp_path_factory.mktemp("src") / "two.mp4", seconds=2.0, size=(320, 240), fps=FPS)


def numbers(path, **kwargs):
    return [clip.frame_number(image) for _t, image in frames.read(path, **kwargs)]


def test_test_clips_carry_their_frame_numbers(two_seconds):
    assert numbers(two_seconds) == list(range(60))


def test_a_trim_starts_and_ends_on_the_exact_frames(two_seconds, tmp_path):
    out = trim.copy(two_seconds, tmp_path / "cut.mp4", start=0.5, end=1.5)

    assert numbers(out) == list(range(15, 45))  # 0.5 s is frame 15; 1.5 s is frame 45, not included
    first_time, _image = next(frames.read(out))
    assert first_time == pytest.approx(0.0, abs=0.001)


def test_a_trim_keeps_the_sound_with_the_picture(two_seconds, tmp_path):
    info = ffmpeg.probe(trim.copy(two_seconds, tmp_path / "cut.mp4", start=0.5, end=1.5))

    assert (info.first("video").codec, info.first("audio").codec) == ("h264", "aac")
    assert info.duration == pytest.approx(1.0, abs=0.05)
    assert info.first("audio").duration == pytest.approx(info.first("video").duration, abs=0.05)


def test_copying_everything_keeps_every_frame(two_seconds, tmp_path):
    out = trim.copy(two_seconds, tmp_path / "copy.mp4")

    assert numbers(out) == list(range(60))
    assert ffmpeg.probe(out).duration == pytest.approx(2.0, abs=0.05)


def test_a_trim_past_the_end_stops_at_the_end(two_seconds, tmp_path):
    out = trim.copy(two_seconds, tmp_path / "tail.mp4", start=1.5, end=99)

    assert numbers(out) == list(range(45, 60))


@pytest.mark.parametrize("start, end", [(1.0, 1.0), (1.5, 0.5), (-0.1, 1.0), (3.0, 4.0)])
def test_an_empty_or_impossible_range_is_refused(two_seconds, tmp_path, start, end):
    with pytest.raises(ValueError):
        trim.copy(two_seconds, tmp_path / "bad.mp4", start=start, end=end)


def test_a_trim_never_overwrites_its_source(two_seconds, tmp_path):
    source = shutil.copy(two_seconds, tmp_path / "source.mp4")

    with pytest.raises(ValueError):
        trim.copy(source, source, start=0.5)


def test_a_failed_trim_leaves_no_half_written_file(tmp_path):
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video" * 100)

    with pytest.raises(ffmpeg.FFmpegError):
        trim.copy(junk, tmp_path / "out.mp4")
    assert not (tmp_path / "out.mp4").exists()


def test_only_local_files_are_written(two_seconds):
    with pytest.raises(ValueError):
        trim.copy(two_seconds, "http://127.0.0.1:9/out.mp4")


def test_names_outside_ascii_work(two_seconds, tmp_path):
    folder = tmp_path / "José ✓"
    folder.mkdir()

    out = trim.copy(two_seconds, folder / "résumé.mp4", start=1.0)

    assert numbers(out) == list(range(30, 60))


def test_frames_at_a_lower_rate_and_size(two_seconds):
    read = list(frames.read(two_seconds, fps=10, width=160))

    assert [round(t, 3) for t, _image in read] == [round(i / 10, 3) for i in range(20)]
    assert {(image.width(), image.height()) for _t, image in read} == {(160, 120)}
    assert [clip.frame_number(image) for _t, image in read] == list(range(0, 60, 3))


def test_frames_from_a_point_on(two_seconds):
    read = list(frames.read(two_seconds, start=1.0, end=1.2))

    assert [clip.frame_number(image) for _t, image in read] == [30, 31, 32, 33, 34, 35]
    assert read[0][0] == pytest.approx(1.0, abs=0.001)
