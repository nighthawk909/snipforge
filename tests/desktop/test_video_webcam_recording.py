"""Desktop: a recording with the real webcam, through the real recorder process. The bubble shows the
camera beside the area (kept out of captures), the recorder draws the camera's pictures into the
frames where the bubble is, and the scene elsewhere is untouched.

Run with: QT_QPA_PLATFORM=windows python -m pytest -m desktop
Skips where there's no camera, or Windows' privacy settings block it.
"""
import time

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor, QGuiApplication

from snipforge.settings import app_settings
from snipforge.video import frames, video_prefs
from snipforge.video.record_flow import RecordFlow
from snipforge.video.video_prefs import RecordingChoices
from tests.desktop.test_video_recording_ui import Scene, kept_out_of_captures, physical

pytestmark = pytest.mark.desktop
SCENE = QColor(40, 90, 160)


@pytest.fixture(scope="module", autouse=True)
def real_platform(qapp):
    if QGuiApplication.platformName() == "offscreen":
        pytest.skip("desktop tests need a real display: set QT_QPA_PLATFORM=windows")


@pytest.fixture(autouse=True)
def quiet_choices():
    app_settings().remove("video")
    yield
    app_settings().remove("video")


def far(color: QColor) -> int:
    """How far a recorded colour is from the scene's (H.264 shifts colours a couple of levels)."""
    return max(abs(color.red() - SCENE.red()), abs(color.green() - SCENE.green()), abs(color.blue() - SCENE.blue()))


def wait_for(condition, seconds=20.0) -> bool:
    deadline = time.perf_counter() + seconds
    while not condition() and time.perf_counter() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    return bool(condition())


def test_a_recording_with_the_webcam_has_the_camera_where_the_bubble_is(qapp, tmp_path):
    from snipforge.video.webcam import Webcam

    if not Webcam().devices():
        pytest.skip("no camera on this machine")
    video_prefs.save(RecordingChoices(microphone=False, system_audio=False, countdown=0, webcam=True,
                                      bubble_size="small"))
    scene = Scene()
    scene.show()
    qapp.processEvents()
    area = physical(scene)
    notes, opened, ended = [], [], []
    flow = RecordFlow(area, scene.screen(), tmp_path, notify=notes.append, open_video=opened.append)
    flow.ended.connect(lambda: ended.append(True))
    flow.start()
    try:
        if not wait_for(lambda: flow.camera.on and flow.camera.webcam.latest() is not None, 10):
            pytest.skip(f"the camera gave no picture: {notes}")
        bubble = flow.camera.bubble.area_rect()
        assert area.contains(bubble) and kept_out_of_captures(int(flow.camera.bubble.winId()))
        flow.panel.record_button.click()
        assert wait_for(lambda: flow.recorder.state == "recording", 30), notes
        wait_for(lambda: False, 1.5)
        flow.stop()
        assert wait_for(lambda: ended, 30) and notes[-1].startswith("Recording saved"), notes
    finally:
        if flow.state != "ended":
            flow.stop()
        scene.close()

    inside = (bubble.center().x() - area.x(), bubble.center().y() - area.y())
    outside = (10, 10)
    last = None
    for _t, image in frames.read(opened[0]):
        last = image
    assert far(last.pixelColor(*outside)) < 12  # the scene, untouched away from the bubble
    assert far(last.pixelColor(*inside)) > 12  # the camera, drawn in where the bubble was
