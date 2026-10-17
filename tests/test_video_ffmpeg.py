"""snipforge/video/ffmpeg.py: reading video files with the FFmpeg libraries Qt Multimedia ships."""
import shutil

import pytest

from snipforge.video import clip, ffmpeg


@pytest.fixture(scope="module")
def test_clip(tmp_path_factory, qapp):
    return clip.make_test_clip(tmp_path_factory.mktemp("clip") / "clip.mp4", seconds=1.0, size=(320, 240))


def test_a_clip_is_described_exactly(test_clip):
    info = ffmpeg.probe(test_clip)

    assert "mp4" in info.format
    video, audio = info.first("video"), info.first("audio")
    assert (video.codec, video.width, video.height) == ("h264", 320, 240)
    assert (audio.codec, audio.sample_rate, audio.channels) == ("aac", 48000, 2)
    assert 0.9 <= info.duration <= 1.1
    assert 25 <= video.frames <= 31


def test_a_file_that_isnt_media_is_reported(tmp_path):
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video" * 100)

    with pytest.raises(ffmpeg.FFmpegError, match="junk.mp4"):
        ffmpeg.probe(junk)


def test_a_missing_file_is_reported(tmp_path):
    with pytest.raises(ffmpeg.FFmpegError, match="nothing.mp4"):
        ffmpeg.probe(tmp_path / "nothing.mp4")


def test_only_local_files_are_opened():
    """The libraries can speak http, rtsp and more; SnipForge never lets them go online."""
    with pytest.raises(ffmpeg.FFmpegError):
        ffmpeg.probe("http://127.0.0.1:9/clip.mp4")


def test_names_outside_ascii_work(test_clip, tmp_path):
    folder = tmp_path / "José ✓ 文件"
    folder.mkdir()
    copy = folder / "résumé.mp4"
    shutil.copy(test_clip, copy)

    assert ffmpeg.probe(copy).first("video").codec == "h264"


def test_only_the_tested_ffmpeg_version_is_used():
    assert ffmpeg.check_versions({"avformat": 63, "avcodec": 63, "avutil": 61}) is None
    with pytest.raises(ffmpeg.FFmpegUnavailable, match="avformat 64"):
        ffmpeg.check_versions({"avformat": 64, "avcodec": 63, "avutil": 61})


def test_the_libraries_are_the_ones_qt_ships():
    versions = ffmpeg.versions()

    assert versions == {"avformat": 63, "avcodec": 63, "avutil": 61}
    assert ffmpeg.license_text().startswith("LGPL")
