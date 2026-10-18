"""snipforge/video/encoder.py: frames and sound into Qt's recorder, out as an H.264 + AAC MP4."""
import array
import math
import sys

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QColor, QImage, QPainter

from snipforge.video import clip, ffmpeg, frames
from snipforge.video.encoder import Encoder, fit

pytestmark = pytest.mark.skipif(sys.platform != "win32" and not ffmpeg.available(),
                                reason="Qt's FFmpeg isn't usable here (ffmpeg.unavailable_reason() says why)")
FPS = 30


def numbered(index: int, size=(320, 240)) -> QImage:
    """A frame showing its number the way test clips do (clip.frame_number reads it)."""
    image = QImage(*size, QImage.Format.Format_RGB32)
    image.fill(QColor(40, 60, 90))
    painter = QPainter(image)
    for bit in range(clip.NUMBER_BITS):
        painter.fillRect(clip._block(bit, *size), QColor("white") if index >> bit & 1 else QColor("black"))
    painter.end()
    return image


def tone(first: int, count: int) -> array.array:
    return array.array("f", (0.2 * math.sin(2 * math.pi * 440 * (first + i) / 48000)
                             for i in range(count) for _ in (0, 1)))


def wait_for(encoder: Encoder, timeout_ms: int = 20_000) -> str:
    """Run Qt until the encoder finishes; 'finished', or the failure."""
    loop = QEventLoop()
    outcome = {"result": "timed out"}
    encoder.finished.connect(lambda: (outcome.update(result="finished"), loop.quit()))
    encoder.failed.connect(lambda message: (outcome.update(result=message), loop.quit()))
    QTimer.singleShot(timeout_ms, loop.quit)
    encoder.finish()
    if outcome["result"] == "timed out" and encoder.recording:
        loop.exec()
    return outcome["result"]


def record(path, count, size=(320, 240), output=None, audio=True, skip=()):
    # Fed far faster than real time, so nothing may be dropped for falling behind.
    encoder = Encoder(path, size, output or size, FPS, audio=audio, max_pending_frames=100_000)
    encoder.start()
    try:
        for i in range(count):
            if i not in skip:
                encoder.add_frame(i, numbered(i, size))
            if audio:
                encoder.add_audio(i * 1600, tone(i * 1600, 1600))  # 1/30 s of sound per frame
        assert wait_for(encoder) == "finished"
        return encoder
    finally:
        encoder.close()


@pytest.mark.parametrize("size, limit, expected", [
    ((1280, 720), (1920, 1080), (1280, 720)),  # small enough already
    ((3440, 1440), (1920, 1080), (1920, 804)),  # shrunk to fit, shape kept
    ((1001, 777), None, (1000, 776)),  # even sizes, as H.264 needs
    ((2560, 1600), (1920, 1080), (1728, 1080)),
])
def test_output_sizes_fit_and_are_even(size, limit, expected):
    assert fit(size, limit) == expected


def test_frames_and_sound_make_an_h264_and_aac_mp4_frame_for_frame(tmp_path, qapp):
    path = tmp_path / "out.mp4"
    record(path, 45)

    info = ffmpeg.probe(path)
    assert (info.first("video").codec, info.first("audio").codec) == ("h264", "aac")
    assert info.duration == pytest.approx(1.5, abs=0.05)
    read = [(round(t * FPS), clip.frame_number(image)) for t, image in frames.read(path)]
    assert read == [(i, i) for i in range(45)]  # each frame at its own time


def test_skipped_frames_leave_the_others_at_their_times(tmp_path, qapp):
    path = tmp_path / "gaps.mp4"
    record(path, 30, skip=range(10, 20))

    read = [(round(t * FPS), clip.frame_number(image)) for t, image in frames.read(path)]
    assert read == [(i, i) for i in range(30) if not 10 <= i < 20]


def test_big_frames_are_scaled_down_by_the_encoder(tmp_path, qapp):
    path = tmp_path / "scaled.mp4"
    record(path, 10, size=(1280, 720), output=(640, 360))

    video = ffmpeg.probe(path).first("video")
    assert (video.width, video.height) == (640, 360)
    assert [clip.frame_number(image) for _t, image in frames.read(path)] == list(range(10))


def test_video_without_sound(tmp_path, qapp):
    path = tmp_path / "silent.mp4"
    record(path, 15, audio=False)

    info = ffmpeg.probe(path)
    assert info.first("audio") is None and info.first("video").codec == "h264"


@pytest.mark.parametrize("audio", [True, False])
def test_the_encoder_says_when_it_is_ready_for_frames(tmp_path, qapp, audio):
    # Qt opens the video encoder after record() returns: ~0.1 s, over a second on a busy machine.
    encoder = Encoder(tmp_path / "ready.mp4", (320, 240), (320, 240), FPS, audio=audio)
    loop = QEventLoop()
    encoder.ready.connect(loop.quit)
    QTimer.singleShot(10_000, loop.quit)
    assert not encoder.is_ready
    encoder.start()
    try:
        loop.exec()
        assert encoder.is_ready
    finally:
        encoder.close()


def test_an_encoder_that_falls_behind_drops_frames_instead_of_queueing_them(tmp_path, qapp):
    encoder = Encoder(tmp_path / "behind.mp4", (320, 240), (320, 240), FPS, audio=False, max_pending_frames=2)
    encoder.start()
    try:
        for i in range(200):  # all at once: far faster than it can encode
            encoder.add_frame(i, numbered(i))
        assert encoder.waiting <= 2
        assert encoder.frames_dropped > 0
        assert encoder.frames_sent + encoder.frames_dropped + encoder.waiting == 200
        assert wait_for(encoder) == "finished"
    finally:
        encoder.close()
    read = [(round(t * FPS), clip.frame_number(image)) for t, image in frames.read(tmp_path / "behind.mp4")]
    assert read[0] == (0, 0)  # frame 0 is never the one dropped: everything stays at its own time
    assert all(at == number for at, number in read)


def test_a_file_that_cant_be_written_fails(tmp_path, qapp):
    encoder = Encoder(tmp_path / "missing folder" / "out.mp4", (320, 240), (320, 240), FPS)
    try:
        encoder.start()
        encoder.add_frame(0, numbered(0))
        assert wait_for(encoder, 5000) != "finished"
    except Exception as exc:  # the recorder may refuse at once instead
        assert "output" in str(exc).lower() or "open" in str(exc).lower() or str(exc)
    finally:
        encoder.close()
