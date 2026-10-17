"""Trimming and copying videos without re-encoding (snipforge/video/trim.py), and reading their
frames (snipforge/video/frames.py), with the FFmpeg libraries Qt ships. Test clips carry each
frame's number as blocks, so "the right frame" is checked exactly.

The main clip is 4 s long: Qt's encoder puts keyframes about 3 s apart, so trims after 3 s really
have to seek, and trims before it really carry hidden frames."""
import shutil
import socket
import sys

import pytest

from snipforge.video import clip, ffmpeg, frames, trim

pytestmark = pytest.mark.skipif(sys.platform != "win32" and not ffmpeg.available(),
                                reason="Qt's FFmpeg isn't usable here (ffmpeg.unavailable_reason() says why)")
FPS = 30


@pytest.fixture(scope="module")
def four_seconds(tmp_path_factory, qapp):
    return clip.make_test_clip(tmp_path_factory.mktemp("src") / "four.mp4", seconds=4.0, size=(320, 240), fps=FPS)


@pytest.fixture(scope="module")
def qt_mkv(tmp_path_factory, qapp):
    """Qt's own Matroska: streams start at about 1 s, with a stray audio packet at 0."""
    return clip.make_test_clip(tmp_path_factory.mktemp("mkv") / "qt.mkv", seconds=2.0, fps=FPS, container="mkv")


def numbers(path, **kwargs):
    return [clip.frame_number(image) for _t, image in frames.read(path, **kwargs)]


def test_test_clips_carry_their_frame_numbers(four_seconds):
    assert numbers(four_seconds) == list(range(120))


@pytest.mark.parametrize("start, end, first, last", [
    (0.5, 1.5, 15, 45),   # inside the first keyframe's group
    (3.0, 3.5, 90, 105),  # starting about where the second keyframe is
    (3.5, 4.0, 105, 120),  # after it: only reachable by seeking
])
def test_a_trim_starts_and_ends_on_the_exact_frames(four_seconds, tmp_path, start, end, first, last):
    out = trim.copy(four_seconds, tmp_path / "cut.mp4", start=start, end=end)

    assert numbers(out) == list(range(first, last))
    assert next(frames.read(out))[0] == pytest.approx(0.0, abs=0.001)
    stored = ffmpeg.probe(out).first("video").frames
    assert stored <= (last - first) + 3 * FPS  # hidden frames: at most one keyframe group, never the whole file


def test_a_trim_keeps_the_sound_with_the_picture(four_seconds, tmp_path):
    info = ffmpeg.probe(trim.copy(four_seconds, tmp_path / "cut.mp4", start=3.5, end=4.0))

    video, audio = info.first("video"), info.first("audio")
    assert (video.codec, audio.codec) == ("h264", "aac")
    assert info.duration == pytest.approx(0.5, abs=0.05)
    assert audio.duration == pytest.approx(video.duration, abs=0.05)
    assert audio.start == pytest.approx(video.start or 0.0, abs=0.025)


def test_copying_everything_keeps_every_frame(four_seconds, tmp_path):
    out = trim.copy(four_seconds, tmp_path / "copy.mp4")

    assert numbers(out) == list(range(120))
    assert ffmpeg.probe(out).duration == pytest.approx(4.0, abs=0.05)


def test_qt_s_own_matroska_is_copied_into_mp4_from_its_first_frame(qt_mkv, tmp_path):
    out = trim.copy(qt_mkv, tmp_path / "from-mkv.mp4")

    assert numbers(out) == list(range(60))
    info = ffmpeg.probe(out)
    assert info.duration == pytest.approx(2.0, abs=0.1)
    assert numbers(trim.copy(qt_mkv, tmp_path / "cut.mp4", start=0.5, end=1.0)) == list(range(15, 30))


def test_a_trim_past_the_end_stops_at_the_end(four_seconds, tmp_path):
    assert numbers(trim.copy(four_seconds, tmp_path / "tail.mp4", start=3.5, end=99)) == list(range(105, 120))


@pytest.mark.parametrize("start, end", [(1.0, 1.0), (1.5, 0.5), (-0.1, 1.0), (4.5, 5.0)])
def test_an_empty_or_impossible_range_is_refused(four_seconds, tmp_path, start, end):
    with pytest.raises(ValueError):
        trim.copy(four_seconds, tmp_path / "bad.mp4", start=start, end=end)
    assert list(tmp_path.iterdir()) == []


def test_a_trim_never_overwrites_its_source(four_seconds, tmp_path):
    source = shutil.copy(four_seconds, tmp_path / "source.mp4")

    with pytest.raises(ValueError):
        trim.copy(source, source, start=0.5)


def test_a_trim_that_fails_half_way_leaves_the_old_file_and_no_leftovers(four_seconds, tmp_path, monkeypatch):
    out = tmp_path / "out.mp4"
    out.write_bytes(b"the trim before")

    def broken(self, *args):
        raise ffmpeg.FFmpegError("disk full")

    monkeypatch.setattr(trim._Copy, "_copy_packets", broken)
    with pytest.raises(ffmpeg.FFmpegError, match="disk full"):
        trim.copy(four_seconds, out, start=1.0)

    assert out.read_bytes() == b"the trim before"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.mp4"]


def test_only_local_files_are_written(four_seconds, tmp_path):
    with pytest.raises(ValueError):
        trim.copy(four_seconds, "http://127.0.0.1:9/out.mp4")
    with pytest.raises(ValueError):
        trim.copy(four_seconds, tmp_path / "out.mp4:hidden")  # an NTFS alternate data stream
    with pytest.raises(ValueError):
        trim.copy(four_seconds, "tcp:127.0.0.1:9")


def test_ffmpeg_itself_refuses_to_write_anywhere_but_a_file(four_seconds):
    """Behind the name checks: given a network address anyway, FFmpeg's whitelist stops it. A
    server is listening, so only the whitelist can keep it from connecting."""
    from pathlib import Path

    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.settimeout(0.5)
        target = Path(f"http://127.0.0.1:{server.getsockname()[1]}/out.mp4")
        with pytest.raises(ffmpeg.FFmpegError):
            trim._Copy(four_seconds, target, 1.0, None).run()
        with pytest.raises(TimeoutError):
            server.accept()  # nobody connected


def test_names_outside_ascii_work(four_seconds, tmp_path):
    folder = tmp_path / "José ✓"
    folder.mkdir()

    out = trim.copy(four_seconds, folder / "résumé.mp4", start=3.0)

    assert numbers(out) == list(range(90, 120))


@pytest.mark.parametrize("fps, count", [(10, 40), (20, 80), (24, 96), (12, 48)])
def test_frames_at_a_lower_rate(four_seconds, fps, count):
    """The rate holds even when it doesn't divide the video's 30 fps."""
    read = list(frames.read(four_seconds, fps=fps))

    assert len(read) == count
    assert all(0 <= t - i / fps < 1 / FPS + 1e-6 for i, (t, _image) in enumerate(read))


def test_frames_shrunk_to_a_width(four_seconds):
    read = list(frames.read(four_seconds, fps=10, width=160, end=1.0))

    assert {(image.width(), image.height()) for _t, image in read} == {(160, 120)}
    assert [clip.frame_number(image) for _t, image in read] == list(range(0, 30, 3))


def test_frames_from_a_point_on(four_seconds):
    read = list(frames.read(four_seconds, start=3.2, end=3.4))

    assert [clip.frame_number(image) for _t, image in read] == [96, 97, 98, 99, 100, 101]
    assert read[0][0] == pytest.approx(3.2, abs=0.001)


def test_frames_of_qt_s_matroska_count_from_its_first_frame(qt_mkv):
    read = list(frames.read(qt_mkv, start=0.5, end=0.6))

    assert [clip.frame_number(image) for _t, image in read] == [15, 16, 17]
    assert read[0][0] == pytest.approx(0.5, abs=0.002)


@pytest.mark.parametrize("kwargs", [{"fps": 0}, {"fps": -5}, {"width": 0}])
def test_impossible_frame_requests_are_refused(four_seconds, kwargs):
    with pytest.raises(ValueError):
        list(frames.read(four_seconds, **kwargs))
