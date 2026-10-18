"""snipforge/video/record_flow.py: one recording from SnipForge's side, with a fake recorder process and
a fake countdown (the recorder process has its own tests, and the countdown its own)."""
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QRect, Signal
from PySide6.QtGui import QGuiApplication

from snipforge.settings import app_settings
from snipforge.video import video_prefs
from snipforge.video.record_flow import RecordFlow
from snipforge.video.recorder import RecordingError, Result
from snipforge.video.video_prefs import RecordingChoices
from tests.test_video_ready import fake_captures

AREA = QRect(100, 120, 640, 360)


@pytest.fixture(autouse=True)
def clean_prefs():
    app_settings().remove("video")
    yield
    app_settings().remove("video")


class FakeRecorder(QObject):
    state_changed = Signal(str)
    warning = Signal(str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, settings, refuse=False):
        super().__init__()
        self.settings, self.calls, self.refuse, self.ended_by = settings, [], refuse, None

    def start(self, hold=False):
        self.calls.append(("start", hold))
        if self.refuse:
            raise RecordingError("SnipForge couldn't start its recorder")

    def go(self):
        self.calls.append("go")

    def stop(self):
        self.calls.append("stop")

    def cancel(self):
        self.calls.append("cancel")
        self.cancelled.emit()

    def finish(self, seconds=4.2, ended_by=None):
        self.ended_by = ended_by
        self.finished.emit(Result(self.settings.output, seconds, {}))


class FakeCountdown(QObject):
    finished = Signal()
    cancelled = Signal()

    def __init__(self, seconds, screen):
        super().__init__()
        self.seconds, self.started, self.closed = seconds, False, False

    def start(self):
        self.started = True

    def close(self):
        self.closed = True


def make_flow(tmp_path, refuse=False):
    made = {"recorders": [], "countdowns": []}
    notes, opened, ended = [], [], []

    def recorder(settings):
        made["recorders"].append(FakeRecorder(settings, refuse))
        return made["recorders"][-1]

    def countdown(seconds, screen):
        made["countdowns"].append(FakeCountdown(seconds, screen))
        return made["countdowns"][-1]

    from snipforge.video.levels import LevelPreview

    flow = RecordFlow(AREA, QGuiApplication.primaryScreen(), tmp_path, notify=notes.append, open_video=opened.append,
                      make_recorder=recorder, make_countdown=countdown, levels=LevelPreview(fake_captures()),
                      devices=lambda: [])
    flow.ended.connect(lambda: ended.append(True))
    flow.start()
    return flow, made, notes, opened, ended


def record_with(flow, choices):
    flow.panel.record.emit(choices)


def test_a_recording_with_a_countdown_from_area_to_saved_file(tmp_path, qapp):
    flow, made, notes, opened, ended = make_flow(tmp_path)
    assert flow.frame.isVisible() and flow.panel.isVisible() and flow.state == "ready"

    record_with(flow, RecordingChoices(countdown=3, clicks=False))
    recorder, countdown = made["recorders"][0], made["countdowns"][0]
    assert recorder.calls == [("start", True)]  # held: the devices open while the countdown runs
    assert countdown.started and countdown.seconds == 3 and flow.state == "counting down"

    countdown.finished.emit()
    assert recorder.calls[-1] == "go" and flow.state == "recording" and flow.recording

    recorder.finish(4.2)
    assert notes == ["Recording saved (0:04)"] and opened == [recorder.settings.output]
    assert ended == [True] and flow.frame is None and not flow.recording


def test_the_recordings_settings_come_from_the_area_and_the_choices(tmp_path, qapp):
    flow, made, *_ = make_flow(tmp_path)

    record_with(flow, RecordingChoices(microphone=False, system_audio=True, highlight=False, countdown=0))

    settings = made["recorders"][0].settings
    assert settings.area == (100, 120, 640, 360)
    assert Path(settings.output).parent.parent == tmp_path and Path(settings.output).name == "recording.mp4"
    assert (settings.microphone, settings.system_audio) == (False, True)
    assert (settings.effects.pointer, settings.effects.highlight, settings.effects.clicks) == (True, False, True)
    assert video_prefs.load().highlight is False  # remembered for next time


def test_without_a_countdown_recording_begins_at_once(tmp_path, qapp):
    flow, made, *_ = make_flow(tmp_path)

    record_with(flow, RecordingChoices(countdown=0))

    assert made["recorders"][0].calls == [("start", False)] and made["countdowns"] == []
    assert flow.state == "recording"


@pytest.mark.parametrize("when, expected", [("counting down", "cancel"), ("recording", "stop")])
def test_stop_cancels_during_the_countdown_and_stops_a_recording(tmp_path, qapp, when, expected):
    flow, made, notes, _opened, ended = make_flow(tmp_path)
    record_with(flow, RecordingChoices(countdown=3))
    if when == "recording":
        made["countdowns"][0].finished.emit()

    flow.stop()

    assert made["recorders"][0].calls[-1] == expected
    if when == "counting down":
        assert made["countdowns"][0].closed and ended == [True] and notes == []


def test_stop_before_recording_closes_the_panel(tmp_path, qapp):
    flow, made, notes, _opened, ended = make_flow(tmp_path)

    flow.stop()

    assert ended == [True] and made["recorders"] == [] and notes == []


def test_a_recording_that_fails_says_why(tmp_path, qapp):
    flow, made, notes, opened, ended = make_flow(tmp_path)
    record_with(flow, RecordingChoices(countdown=0))

    made["recorders"][0].failed.emit("SnipForge's recorder stopped unexpectedly")

    assert notes == ["The recording failed: SnipForge's recorder stopped unexpectedly"] and opened == []
    assert ended == [True]


def test_a_recorder_that_cant_start_says_why(tmp_path, qapp):
    flow, _made, notes, _opened, ended = make_flow(tmp_path, refuse=True)

    record_with(flow, RecordingChoices(countdown=3))

    assert notes == ["The recording couldn't start: SnipForge couldn't start its recorder"] and ended == [True]


def test_a_recording_windows_ended_says_so(tmp_path, qapp):
    flow, made, notes, *_ = make_flow(tmp_path)
    record_with(flow, RecordingChoices(countdown=0))

    made["recorders"][0].finish(65, ended_by="sleep")

    assert notes == ["Recording saved (1:05): it stopped because Windows went to sleep"]


def test_warnings_from_the_recorder_are_passed_on(tmp_path, qapp):
    flow, made, notes, *_ = make_flow(tmp_path)
    record_with(flow, RecordingChoices(countdown=0))

    made["recorders"][0].warning.emit("The microphone was unplugged")

    assert notes == ["The microphone was unplugged"]


def test_stop_during_the_countdown_ends_at_once_even_if_the_recorder_never_answers(tmp_path, qapp):
    flow, made, notes, _opened, ended = make_flow(tmp_path)
    record_with(flow, RecordingChoices(countdown=3))
    recorder = made["recorders"][0]
    recorder.cancel = lambda: recorder.calls.append("cancel")  # sent, but no reply comes

    flow.stop()
    flow.stop()  # pressed twice

    assert recorder.calls[-1] == "cancel" and ended == [True] and flow.state == "ended"


def test_a_late_recorder_process_says_so_when_the_countdown_ends(tmp_path, qapp):
    flow, made, notes, *_ = make_flow(tmp_path)
    record_with(flow, RecordingChoices(countdown=3))
    made["recorders"][0].state = "launching"  # the process isn't up yet

    made["countdowns"][0].finished.emit()

    assert notes == ["Starting the recorder: recording begins in a moment"]


def test_a_recording_that_cant_be_opened_says_where_it_was_saved(tmp_path, qapp):
    flow, made, notes, *_ = make_flow(tmp_path)
    record_with(flow, RecordingChoices(countdown=0))

    def broken(path):
        raise OSError("no editor today")

    flow._open_video = broken
    made["recorders"][0].finish(3)

    assert notes[-1].startswith("Recording saved (0:03), but it couldn't be opened:")
    assert str(made["recorders"][0].settings.output) in notes[-1]


def test_the_flow_ends_even_if_the_note_cant_be_shown(tmp_path, qapp):
    flow, made, _notes, _opened, ended = make_flow(tmp_path)
    record_with(flow, RecordingChoices(countdown=0))

    def broken(note):
        raise RuntimeError("no tray")

    flow._notify = broken
    with pytest.raises(RuntimeError):
        flow._failed("x")  # called directly: from a signal, PySide would only print the error

    assert ended == [True]
