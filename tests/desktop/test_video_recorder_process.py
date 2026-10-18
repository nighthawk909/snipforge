"""Desktop: a recording made by the real recorder process (`python -m snipforge --record`), and
Windows' sleep message making it finish the file before Windows goes on.

Run with: QT_QPA_PLATFORM=windows python -m pytest -m desktop
"""
import ctypes
from ctypes import wintypes

import pytest
from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from snipforge.video import ffmpeg, power
from snipforge.video.recorder import Settings
from snipforge.video.remote_recorder import RemoteRecorder

pytestmark = pytest.mark.desktop
SMTO_BLOCK = 0x0001


@pytest.fixture(scope="module", autouse=True)
def real_platform(qapp):
    if QGuiApplication.platformName() == "offscreen":
        pytest.skip("desktop tests need a real display: set QT_QPA_PLATFORM=windows")


class Box(QWidget):
    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setGeometry(160, 160, 400, 240)

    def paintEvent(self, _event):  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(20, 120, 200))
        painter.end()


def area_of(widget) -> tuple[int, int, int, int]:
    screen = widget.screen()
    ratio, origin = screen.devicePixelRatio(), screen.geometry().topLeft()
    g = widget.geometry()
    return (origin.x() + round((g.x() - origin.x()) * ratio), origin.y() + round((g.y() - origin.y()) * ratio),
            round(g.width() * ratio) // 2 * 2, round(g.height() * ratio) // 2 * 2)


def record(tmp_path, while_recording):
    """Start a RemoteRecorder on a test window; `while_recording(remote)` runs 1 s after it begins."""
    box = Box()
    box.show()
    remote = RemoteRecorder(Settings(area_of(box), tmp_path / "recording.mp4", microphone=False,
                                     system_audio=False))
    loop, outcome = QEventLoop(), {}
    remote.finished.connect(lambda result: (outcome.update(result=result), loop.quit()))
    remote.failed.connect(lambda message: (outcome.update(failure=message), loop.quit()))
    remote.state_changed.connect(lambda state: state == "recording" and QTimer.singleShot(
        1000, lambda: while_recording(remote, outcome)))
    remote.start()
    QTimer.singleShot(60_000, loop.quit)
    loop.exec()
    box.close()
    if "failure" in outcome and "screen" in outcome["failure"].lower():
        pytest.skip(f"this machine can't record its screen: {outcome['failure']}")
    assert "result" in outcome, outcome.get("failure", "the recorder never finished")
    return remote, outcome


def test_a_recording_made_in_its_own_process(qapp, tmp_path):
    remote, outcome = record(tmp_path, lambda remote, _outcome: QTimer.singleShot(500, remote.stop))

    result = outcome["result"]
    info = ffmpeg.probe(result.path)
    assert info.first("video").codec == "h264" and 1.2 < result.duration < 2.5
    assert remote.pid > 0 and remote.ended_by is None


def test_windows_going_to_sleep_finishes_the_recording_first(qapp, tmp_path):
    def sleep(remote, outcome):
        user32 = ctypes.WinDLL("user32")
        user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                               wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        answer = ctypes.c_size_t()
        sent = user32.SendMessageTimeoutW(remote.window_id, power.WM_POWERBROADCAST, power.PBT_APMSUSPEND, 0,
                                          SMTO_BLOCK, 5000, ctypes.byref(answer))
        # SendMessage returns once the recorder has handled it: the file must be whole already.
        outcome["sent"] = sent
        outcome["codec_then"] = ffmpeg.probe(remote.settings.output).first("video").codec

    remote, outcome = record(tmp_path, sleep)

    assert outcome["sent"] and outcome["codec_then"] == "h264"
    assert remote.ended_by == "sleep"
