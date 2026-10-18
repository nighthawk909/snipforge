"""Desktop: a recording made by the real recorder process (`python -m snipforge --record`), and
Windows' sleep and session-end messages making it finish the file before Windows goes on. Each
checks the recorder process then exits cleanly (a crash on the way out would show in its exit code).

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
ENDSESSION_LOGOFF = 0x80000000


class Process:
    """A handle on the recorder process, opened while it runs so its exit code can be read after."""

    def __init__(self, pid: int):
        self.kernel32 = k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = ctypes.c_void_p
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        k.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        self.handle = k.OpenProcess(0x1000 | 0x00100000, False, pid)  # query, synchronize
        assert self.handle, f"can't open the recorder process {pid}"

    def exit_code(self, seconds: float = 15) -> int:
        k = self.kernel32
        assert k.WaitForSingleObject(self.handle, int(seconds * 1000)) == 0, "the recorder process didn't exit"
        code = wintypes.DWORD()
        k.GetExitCodeProcess(self.handle, ctypes.byref(code))
        k.CloseHandle(self.handle)
        return code.value


def send_and_wait(window: int, message: int, wparam: int, lparam: int = 0) -> int:
    """SendMessageTimeout: returns once the recorder process has handled the message (or after 10 s)."""
    user32 = ctypes.WinDLL("user32")
    user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                           wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    answer = ctypes.c_size_t()
    sent = user32.SendMessageTimeoutW(window, message, wparam, lparam, SMTO_BLOCK, 10_000, ctypes.byref(answer))
    assert sent, "the recorder process didn't handle the message in time"
    return answer.value


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

    def began(state):
        if state == "recording":
            outcome["process"] = Process(remote.pid)
            QTimer.singleShot(1000, lambda: while_recording(remote, outcome))

    remote.state_changed.connect(began)
    remote.start()
    QTimer.singleShot(60_000, loop.quit)
    loop.exec()
    box.close()
    assert "result" in outcome, outcome.get("failure", "the recorder never finished")
    assert outcome["process"].exit_code() == 0
    return remote, outcome


def test_a_recording_made_in_its_own_process(qapp, tmp_path):
    remote, outcome = record(tmp_path, lambda remote, _outcome: QTimer.singleShot(500, remote.stop))

    result = outcome["result"]
    info = ffmpeg.probe(result.path)
    # Stopped 1.5 s after SnipForge heard "recording"; a slow runner delivers that stop late.
    assert info.first("video").codec == "h264" and 1.2 < result.duration < 6
    # Frames on time whatever the screen does (a software graphics driver once let 3 of 90 through).
    assert result.stats["frames_made"] >= 0.8 * result.duration * 30, result.stats
    assert remote.pid > 0 and remote.ended_by is None


def test_windows_going_to_sleep_finishes_the_recording_first(qapp, tmp_path):
    def sleep(remote, outcome):
        send_and_wait(remote.window_id, power.WM_POWERBROADCAST, power.PBT_APMSUSPEND)
        # Handled, so Windows would sleep now: the file must already be whole.
        outcome["codec_then"] = ffmpeg.probe(remote.settings.output).first("video").codec

    remote, outcome = record(tmp_path, sleep)

    assert outcome["codec_then"] == "h264" and remote.ended_by == "sleep"


def test_signing_out_finishes_the_recording_first(qapp, tmp_path):
    def sign_out(remote, outcome):
        outcome["may_end"] = send_and_wait(remote.window_id, power.WM_QUERYENDSESSION, 0, ENDSESSION_LOGOFF)
        send_and_wait(remote.window_id, power.WM_ENDSESSION, 1, ENDSESSION_LOGOFF)
        # Handled, so Windows would end the session now: the file must already be whole.
        outcome["codec_then"] = ffmpeg.probe(remote.settings.output).first("video").codec

    remote, outcome = record(tmp_path, sign_out)

    assert outcome["may_end"] == 1 and outcome["codec_then"] == "h264"
    assert remote.ended_by == "session end"
