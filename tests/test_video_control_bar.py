"""snipforge/video/control_bar.py: the recording's controls, with a fake recorder."""
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtTest import QTest

from snipforge.video import control_bar
from snipforge.video.control_bar import ControlBar
from snipforge.video.recorder import Settings


class FakeRecorder(QObject):
    state_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.state, self.calls, self.seconds, self.loudness = "recording", [], 0.0, {}

    def media_time(self):
        return self.seconds

    def levels(self):
        return dict(self.loudness)

    def _call(self, name, state=None):
        self.calls.append(name)
        if state:
            self.state = state
            self.state_changed.emit(state)

    def pause(self):
        self._call("pause", "paused")

    def resume(self):
        self._call("resume", "recording")

    def stop(self):
        self._call("stop", "finishing")

    def cancel(self):
        self._call("cancel", "cancelled")


def make_bar(microphone=True, system_audio=False):
    recorder = FakeRecorder()
    settings = Settings((100, 100, 640, 360), Path("C:/rec.mp4"), microphone=microphone, system_audio=system_audio)
    bar = ControlBar(recorder, settings)
    bar.show()
    return bar, recorder


def test_it_shows_the_time_recorded_and_a_meter_for_each_sound(qapp):
    bar, recorder = make_bar(microphone=True, system_audio=False)
    recorder.seconds, recorder.loudness = 65.2, {"microphone": 0.5}

    bar.refresh()

    assert bar.time.text() == "1:05"
    assert list(bar.meters) == ["microphone"] and bar.meters["microphone"].level == 0.5
    bar.close()


def test_pause_and_resume(qapp):
    bar, recorder = make_bar()

    QTest.mouseClick(bar.pause_button, Qt.LeftButton)
    assert recorder.calls == ["pause"] and bar.pause_button.text() == " Resume" and bar.dot.text() == "❚❚"
    QTest.mouseClick(bar.pause_button, Qt.LeftButton)

    assert recorder.calls == ["pause", "resume"] and bar.pause_button.text() == " Pause"
    bar.close()


def test_stop_saves_and_the_bar_goes(qapp):
    bar, recorder = make_bar()

    QTest.mouseClick(bar.stop_button, Qt.LeftButton)

    assert recorder.calls == ["stop"] and not bar.isVisible()


def test_discard_asks_again_on_the_same_button(qapp):
    bar, recorder = make_bar()

    QTest.mouseClick(bar.discard_button, Qt.LeftButton)
    assert recorder.calls == [] and bar.discard_button.text() == " Discard?"
    QTest.mouseClick(bar.discard_button, Qt.LeftButton)

    assert recorder.calls == ["cancel"] and not bar.isVisible()


def test_an_unanswered_discard_goes_back_to_how_it_was(qapp, monkeypatch):
    monkeypatch.setattr(control_bar, "DISARM_MS", 10)
    bar, recorder = make_bar()
    QTest.mouseClick(bar.discard_button, Qt.LeftButton)

    QTest.qWait(50)
    QTest.mouseClick(bar.discard_button, Qt.LeftButton)

    assert recorder.calls == [] and bar.discard_button.text() == " Discard?"  # armed afresh, not discarded
    bar.close()


def test_it_never_takes_the_keyboard_from_the_app_being_recorded(qapp):
    bar, _recorder = make_bar()
    assert bar.testAttribute(Qt.WA_AlwaysShowToolTips)  # tooltips though it's never the active window

    assert bar.windowFlags() & Qt.WindowDoesNotAcceptFocus
    assert all(b.focusPolicy() == Qt.NoFocus for b in (bar.pause_button, bar.stop_button, bar.discard_button))
    bar.close()
