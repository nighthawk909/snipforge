"""Screen recording's encoder (Qt Multimedia and the FFmpeg it ships): a short clip is encoded the
way recordings are. The packaged self-test runs the same code, so a packaging mistake fails the build."""
import subprocess
import sys

import pytest

from snipforge.video import clip, ffmpeg

# Off Windows, Qt 6.12 has crashed (segfault) while deleting a recorder that had failed or been
# abandoned.
windows_teardown = pytest.mark.skipif(sys.platform != "win32",
                                      reason="Qt has crashed tearing down a failed recorder off Windows")
needs_ffmpeg = pytest.mark.skipif(sys.platform != "win32" and not ffmpeg.available(),
                                   reason="Qt's FFmpeg isn't usable here (ffmpeg.unavailable_reason() says why)")


@needs_ffmpeg
def test_a_test_clip_is_h264_video_with_aac_sound(tmp_path, qapp):
    """Checked, not assumed: Qt switches to another codec without saying so when a container has no
    H.264 (it did for Matroska, writing MPEG-4 Part 2, which browsers can't play)."""
    path = clip.make_test_clip(tmp_path / "clip.mp4", seconds=0.5, size=(320, 240))

    info = ffmpeg.probe(path)

    assert (info.first("video").codec, info.first("audio").codec) == ("h264", "aac")
    assert (info.first("video").width, info.first("video").height) == (320, 240)
    assert 0.4 <= info.duration <= 0.6


def test_a_clip_that_cant_be_written_is_reported(tmp_path, qapp):
    folder = tmp_path / "taken"
    folder.mkdir()  # a folder where the file should go

    with pytest.raises(clip.ClipError):
        clip.make_test_clip(folder, seconds=0.2)


@windows_teardown
def test_a_recorder_error_is_reported(tmp_path, qapp):
    with pytest.raises(clip.ClipError, match="Couldn't record"):
        clip.make_test_clip(tmp_path / "no such folder" / "clip.mp4", seconds=0.2)


@windows_teardown
def test_a_recording_that_takes_too_long_is_stopped_and_lets_go_of_its_file(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr(clip, "TIMEOUT_MS", 1)
    path = tmp_path / "slow.mp4"

    with pytest.raises(clip.ClipError, match="timed out"):
        clip.make_test_clip(path, seconds=5.0)
    path.unlink(missing_ok=True)  # Windows refuses to delete a file that's still open


def test_recorders_dont_outlive_their_clip(tmp_path, qapp):
    import gc

    from PySide6.QtMultimedia import QMediaRecorder

    def alive():
        gc.collect()
        return sum(isinstance(o, QMediaRecorder) for o in gc.get_objects())

    before = alive()
    for i in range(3):
        clip.make_test_clip(tmp_path / f"clip{i}.mp4", seconds=0.2)

    assert alive() == before


def test_a_relative_name_is_kept_where_it_says(tmp_path, monkeypatch, qapp):
    monkeypatch.chdir(tmp_path)

    path = clip.make_test_clip("relative.mp4", seconds=0.2)

    assert path == tmp_path / "relative.mp4" and path.is_file()


def test_the_app_doesnt_load_qt_multimedia_until_it_records():
    code = ("import sys; import snipforge.app, snipforge.video.clip, snipforge.video.ffmpeg; "
            "sys.exit(1 if 'PySide6.QtMultimedia' in sys.modules else 0)")
    assert subprocess.run([sys.executable, "-c", code], timeout=60).returncode == 0


@needs_ffmpeg
def test_a_clip_that_comes_out_wrong_is_recorded_again(tmp_path, monkeypatch, caplog, qapp):
    real = clip._record
    tries = []

    def first_one_broken(path, *args):
        tries.append(path)
        if len(tries) == 1:
            path.write_bytes(b"[00:00.00] not an mp4")
        else:
            real(path, *args)

    monkeypatch.setattr(clip, "_record", first_one_broken)

    path = clip.make_test_clip(tmp_path / "clip.mp4", seconds=0.2)

    assert len(tries) == 2 and ffmpeg.probe(path).first("video").codec == "h264"
    assert "didn't come out as MP4" in caplog.text


def test_a_clip_that_comes_out_wrong_twice_is_reported(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr(clip, "_record", lambda path, *args: path.write_bytes(b"junk" * 10))

    with pytest.raises(clip.ClipError, match="isn't an MP4"):
        clip.make_test_clip(tmp_path / "clip.mp4", seconds=0.2)
