"""Desktop test: the real camera sends pictures (snipforge/video/webcam.py).

Switches the default camera on for a second or two. Skipped where there's no camera, or Windows'
privacy setting or another app keeps it from SnipForge. Run with:

    QT_QPA_PLATFORM=windows python -m pytest -m desktop tests/desktop/test_video_camera.py
"""
import time

import pytest
from PySide6.QtGui import QImage

from snipforge.video import webcam

pytestmark = pytest.mark.desktop

PICTURES = 10
TIMEOUT = 6.0  # seconds; opening a camera takes ~0.2 s, a few seconds for some USB ones


def test_the_default_camera_sends_pictures(qapp):
    camera = webcam.Webcam()
    if not camera.devices():
        pytest.skip("no camera on this machine")
    failures, shown = [], []
    camera.failed.connect(failures.append)
    camera.frame.connect(shown.append)
    latest = None
    try:
        if camera.start():
            deadline = time.perf_counter() + TIMEOUT
            while time.perf_counter() < deadline and not failures and len(shown) < PICTURES:
                qapp.processEvents()
                time.sleep(0.01)
            latest = camera.latest()
    finally:
        camera.stop()

    if failures and failures[0] in (webcam.blocked_message(), webcam.IN_USE, webcam.NO_PICTURE):
        pytest.skip(failures[0])
    assert failures == [] and len(shown) >= PICTURES
    assert latest is not None and latest.format() == QImage.Format_RGB32 and latest.height() >= 240
    assert camera.latest() is None and not camera.is_active
