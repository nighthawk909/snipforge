"""Desktop: the recording UI end to end, with the real recorder process. The ready panel's Record,
the countdown, the control bar in the recorder process (kept out of captures, like the outline), and
stop: a saved H.264 file, opened, and a recorder process that exits cleanly.

Run with: QT_QPA_PLATFORM=windows python -m pytest -m desktop
"""
import ctypes
import time
from ctypes import wintypes

import pytest
from PySide6.QtCore import QCoreApplication, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from snipforge.settings import app_settings
from snipforge.video import ffmpeg, video_prefs
from snipforge.video.record_flow import RecordFlow
from snipforge.video.video_prefs import RecordingChoices
from tests.desktop.test_video_recording_process import Process

pytestmark = pytest.mark.desktop
WDA_EXCLUDEFROMCAPTURE = 0x11


@pytest.fixture(scope="module", autouse=True)
def real_platform(qapp):
    if QGuiApplication.platformName() == "offscreen":
        pytest.skip("desktop tests need a real display: set QT_QPA_PLATFORM=windows")


@pytest.fixture(autouse=True)
def quiet_choices():
    app_settings().remove("video")
    yield
    app_settings().remove("video")


class Scene(QWidget):
    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setGeometry(200, 200, 480, 270)

    def paintEvent(self, _event):  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(40, 90, 160))
        painter.end()


def wait_for(condition, seconds=20.0) -> bool:
    deadline = time.perf_counter() + seconds
    while not condition() and time.perf_counter() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    return bool(condition())


def physical(widget) -> QRect:
    screen = widget.screen()
    ratio, origin = screen.devicePixelRatio(), screen.geometry().topLeft()
    g = widget.geometry()
    return QRect(origin.x() + round((g.x() - origin.x()) * ratio), origin.y() + round((g.y() - origin.y()) * ratio),
                 round(g.width() * ratio) // 2 * 2, round(g.height() * ratio) // 2 * 2)


def kept_out_of_captures(hwnd: int) -> bool:
    affinity = wintypes.DWORD()
    user32 = ctypes.WinDLL("user32")
    user32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    return bool(user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(affinity))) and \
        affinity.value == WDA_EXCLUDEFROMCAPTURE


def visible_windows_of(pid: int) -> list[int]:
    user32 = ctypes.WinDLL("user32")
    found = []

    def each(hwnd, _param):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(each), 0)
    return found


@pytest.mark.parametrize("countdown", [0, 3])
def test_record_from_the_ready_panel_to_the_saved_file(qapp, tmp_path, countdown):
    video_prefs.save(RecordingChoices(microphone=False, system_audio=False, countdown=countdown))
    scene = Scene()
    scene.show()
    qapp.processEvents()
    notes, opened, ended = [], [], []
    flow = RecordFlow(physical(scene), scene.screen(), tmp_path, notify=notes.append, open_video=opened.append)
    flow.ended.connect(lambda: ended.append(True))
    flow.start()
    try:
        assert flow.panel.isVisible() and kept_out_of_captures(int(flow.frame.winId()))
        flow.panel.record_button.click()
        assert wait_for(lambda: flow.recorder.state == "recording", 30), (flow.recorder.state, notes)
        recorder_process = Process(flow.recorder.pid)
        assert wait_for(lambda: visible_windows_of(flow.recorder.pid), 5)  # its control bar
        assert all(kept_out_of_captures(hwnd) for hwnd in visible_windows_of(flow.recorder.pid))
        wait_for(lambda: False, 1.5)

        flow.stop()

        assert wait_for(lambda: ended, 30), notes
        assert notes and notes[-1].startswith("Recording saved"), notes
        assert ffmpeg.probe(opened[0]).first("video").codec == "h264"
        assert recorder_process.exit_code() == 0
    finally:
        if flow.state != "ended":
            flow.stop()
        scene.close()
