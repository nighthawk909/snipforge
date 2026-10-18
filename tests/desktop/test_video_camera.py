"""Desktop test: the real camera sends pictures (snipforge/video/webcam.py).

Switches the default camera on for a second or two. Skipped where there's no camera, or Windows'
privacy setting or another app keeps it from SnipForge. Run with:

    QT_QPA_PLATFORM=windows python -m pytest -m desktop tests/desktop/test_video_camera.py
"""
import time

import pytest
from PySide6.QtCore import QThread
from PySide6.QtGui import QImage

from snipforge.video import webcam

pytestmark = pytest.mark.desktop

PICTURES = 10
TIMEOUT = 6.0  # seconds; opening a camera takes ~0.2 s, a few seconds for some USB ones


def test_the_default_camera_sends_pictures(qapp):
    camera = webcam.Webcam()
    found = camera.devices()
    if not found:
        pytest.skip("no camera on this machine")
    assert all(isinstance(i, str) and i and isinstance(name, str) for i, name in found)
    failures, shown, on_main_thread = [], [], []
    camera.failed.connect(failures.append)
    camera.frame.connect(shown.append)
    camera.frame.connect(lambda _image: on_main_thread.append(QThread.currentThread() is qapp.thread()))
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

    if failures and failures[0] in (webcam.blocked_message(), webcam.IN_USE):
        pytest.skip(failures[0])
    assert failures == [] and len(shown) >= PICTURES
    assert on_main_thread and all(on_main_thread)  # where webcam.py expects them
    assert latest is not None and latest.format() == QImage.Format_RGB32 and latest.height() >= 240
    assert camera.latest() is None and not camera.is_active
