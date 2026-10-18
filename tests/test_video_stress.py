"""Stress: hundreds of recordings through Qt's encoder, every file checked with FFmpeg.

Looks for the rare bad file seen in early test runs (about 1 test run in 65, a fresh test clip read
back as another format: docs/VIDEO-PLAN.md, Risks). Slow, so not in the regular run:

    python -m pytest -m stress
"""
import array
import math

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QColor, QImage

from snipforge.video import ffmpeg
from snipforge.video.encoder import Encoder

pytestmark = pytest.mark.stress
RECORDINGS = 200
FRAMES = 12


def one_recording(path, frame_size=(320, 240)) -> str:
    encoder = Encoder(path, frame_size, frame_size, 30, max_pending_frames=10_000)
    loop, outcome = QEventLoop(), {}
    encoder.finished.connect(lambda: (outcome.update(result="finished"), loop.quit()))
    encoder.failed.connect(lambda message: (outcome.update(result=message), loop.quit()))
    encoder.start()
    try:
        for i in range(FRAMES):
            image = QImage(*frame_size, QImage.Format.Format_RGB32)
            image.fill(QColor.fromHsv(i * 20 % 360, 200, 200))
            encoder.add_frame(i, image)
            encoder.add_audio(i * 1600, array.array("f", (0.1 * math.sin(i * 1600 + n) for n in range(3200))))
        encoder.finish()
        QTimer.singleShot(20_000, loop.quit)
        if not outcome:
            loop.exec()
    finally:
        encoder.close()
    return outcome.get("result", "timed out")


def test_every_recording_comes_out_whole(tmp_path, qapp):
    problems = []
    for n in range(RECORDINGS):
        path = tmp_path / f"r{n}.mp4"
        outcome = one_recording(path)
        try:
            info = ffmpeg.probe(path)
            whole = ("mp4" in info.format and info.first("video").codec == "h264"
                     and info.first("audio").codec == "aac" and 0.3 < (info.duration or 0) < 0.5)
            if outcome != "finished" or not whole:
                problems.append((n, outcome, info))
        except ffmpeg.FFmpegError as exc:
            problems.append((n, outcome, str(exc), path.read_bytes()[:16]))
    assert problems == []
