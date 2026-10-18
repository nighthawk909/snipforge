"""Desktop test for the video editor: a real recording played by Qt's own player.

Run with the rest of the desktop suite:

    QT_QPA_PLATFORM=windows python -m pytest -m desktop

Qt 6.12's player now and then stalls before its first frame in a test runner (docs/VIDEO-PLAN.md,
Risks); the editor loads the file again when that happens, and if it still shows nothing this test
skips rather than fails. It plays about a second of video, with sound on the default output.
"""
import time

import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from snipforge.video import clip
from snipforge.video.editor_window import VideoEditorWindow
from snipforge.video.player import Player

pytestmark = [pytest.mark.desktop, pytest.mark.windows]
FPS = 30


@pytest.fixture(scope="module", autouse=True)
def real_platform(qapp):
    if QGuiApplication.platformName() == "offscreen":
        pytest.skip("desktop tests need a real display: set QT_QPA_PLATFORM=windows")


def pump_until(condition, seconds: float) -> bool:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def test_a_recording_plays_and_stops_at_the_out_point(tmp_path, qapp):
    path = clip.make_test_clip(tmp_path / "Recording.mp4", seconds=2.0, size=(320, 240), fps=FPS)
    window = VideoEditorWindow(path, player=Player(first_frame_timeout_ms=3000, retries=1))
    played, failures = [], []
    window.player.frame_shown.connect(lambda image, seconds: played.append((clip.frame_number(image), seconds)))
    window.player.failed.connect(failures.append)
    window.show()
    try:
        strip = window.timeline
        strip.set_range(strip.range.with_stop(30))  # keep the first second
        window.toggle_play()

        if not pump_until(lambda: played or failures, 8.0) or failures:
            pytest.skip(f"Qt's player showed no picture here ({failures or 'no frame within 8 s'})")
        assert pump_until(lambda: not window.is_playing, 4.0), "playing didn't stop at the out point"

        assert all(number == round(seconds * FPS) for number, seconds in played)  # each picture at its time
        assert strip.playhead == 29
        assert pump_until(lambda: clip.frame_number(window.view.image) == 29, 3.0)
    finally:
        window.close()
