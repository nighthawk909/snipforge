"""snipforge/video/record_flow.py: one recording from SnipForge's side, with a fake recorder process and
a fake countdown (the recorder process has its own tests, and the countdown its own)."""
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QRect, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

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


class FakeBar:
    def __init__(self, recorder, settings):
        self.recorder, self.settings, self.shown, self.closed = recorder, settings, False, False

    def show(self):
        self.shown = True

    def close(self):
        self.closed = True


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


def make_flow(tmp_path, refuse=False, **more):
    made = {"recorders": [], "countdowns": [], "bars": []}
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
                      devices=lambda: [], make_bar=lambda r, s: made["bars"].append(FakeBar(r, s)) or made["bars"][-1],
                      **more)
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


def test_the_control_bar_shows_while_recording_and_goes_at_the_end(tmp_path, qapp):
    flow, made, *_ = make_flow(tmp_path)
    record_with(flow, RecordingChoices(countdown=0))
    recorder = made["recorders"][0]
    assert made["bars"] == []  # not before recording begins

    recorder.state_changed.emit("recording")
    bar = made["bars"][0]
    assert bar.shown and bar.recorder is recorder and bar.settings == recorder.settings

    recorder.finish(2)
    assert bar.closed and flow.bar is None


# --- the webcam -----------------------------------------------------------------------------------

from PySide6.QtGui import QColor, QImage  # noqa: E402

from snipforge.video import camera_share  # noqa: E402


class FakeWebcam(QObject):
    frame = Signal(QImage)
    stopped = Signal()
    failed = Signal(str)
    refuse = None

    def __init__(self):
        super().__init__()
        self.calls = []

    def start(self, device_id=None):
        self.calls.append(("start", device_id))
        if self.refuse:
            self.failed.emit(self.refuse)
            return False
        return True

    def stop(self):
        self.calls.append("stop")

    def devices(self):
        return [("{cam}", "Laptop camera")]


class FakeBubble(QObject):
    moved = Signal(QRect)
    shape_changed = Signal(str)
    size_changed = Signal(str)

    def __init__(self, camera, shape, size):
        super().__init__()
        self.camera, self._shape, self.size, self.rect = camera, shape, size, QRect(0, 0, 192, 192)
        self.shown = self.closed = False

    def show(self):
        self.shown = True

    def close(self):
        self.closed = True

    def area_rect(self):
        return QRect(self.rect)

    def shape(self):
        return self._shape

    def bubble_size(self):
        return self.size

    def snapshot(self):
        from snipforge.video.webcam_bubble import BubbleSnapshot

        return None if self.closed else BubbleSnapshot(self.rect.getRect(), self._shape)

    def move_physical(self, x, y):
        self.rect.moveTo(x, y)


def camera_flow(tmp_path, choices, refuse=None):
    made = {"webcams": [], "bubbles": []}
    FakeWebcam.refuse = refuse

    def webcam():
        made["webcams"].append(FakeWebcam())
        return made["webcams"][-1]

    def bubble(camera, shape, size):
        made["bubbles"].append(FakeBubble(camera, shape, size))
        return made["bubbles"][-1]

    video_prefs.save(choices)
    flow, flow_made, notes, opened, ended = make_flow(tmp_path, make_webcam=webcam, make_bubble=bubble)
    made.update(flow_made)
    return flow, made, notes, ended


def test_with_the_camera_on_its_bubble_shows_beside_the_area_while_getting_ready(tmp_path, qapp):
    flow, made, *_ = camera_flow(tmp_path, RecordingChoices(webcam=True, webcam_id="{cam}", bubble_shape="rounded"))

    webcam, bubble = made["webcams"][0], made["bubbles"][0]
    assert webcam.calls == [("start", "{cam}")] and bubble.shown and bubble.shape() == "rounded"
    assert AREA.contains(bubble.rect)  # inside the area, at its bottom right
    assert bubble.rect.bottomRight().x() > AREA.center().x() and bubble.rect.bottom() > AREA.center().y()


def test_a_bubble_left_somewhere_comes_back_there(tmp_path, qapp):
    flow, made, *_ = camera_flow(tmp_path, RecordingChoices(webcam=True, bubble_x=30, bubble_y=40))

    assert made["bubbles"][0].rect.topLeft().toTuple() == (30, 40)


def test_switching_the_camera_on_and_off_on_the_panel(tmp_path, qapp):
    flow, made, *_ = camera_flow(tmp_path, RecordingChoices(webcam=False))
    assert made["webcams"] == []

    flow.panel.camera_button.click()
    assert made["bubbles"][0].shown and made["webcams"][0].calls == [("start", None)]
    flow.panel.camera_button.click()

    assert made["webcams"][0].calls[-1] == "stop" and made["bubbles"][0].closed


def test_a_camera_that_cant_start_says_why_and_switches_off(tmp_path, qapp):
    flow, made, notes, _ended = camera_flow(tmp_path, RecordingChoices(webcam=True), refuse="The camera is in use")

    assert notes == ["The camera is in use"] and made["bubbles"][0].closed
    assert not flow.panel.camera_button.isChecked()


def test_recording_with_the_camera_shares_its_pictures_and_bubble(tmp_path, qapp):
    flow, made, *_ = camera_flow(tmp_path, RecordingChoices(webcam=True, countdown=0, bubble_shape="circle"))
    bubble, webcam = made["bubbles"][0], made["webcams"][0]

    record_with(flow, flow.panel.choices)
    settings = made["recorders"][0].settings
    reader = camera_share.CameraReader(settings.camera_share)
    image = QImage(64, 48, QImage.Format.Format_RGB32)
    image.fill(QColor("#ff8800"))
    webcam.frame.emit(image)

    assert reader.latest().pixelColor(3, 3).name() == "#ff8800"  # the recorder sees the camera's pictures
    assert settings.camera_bubble == bubble.rect.getRect() and settings.camera_shape == "circle"
    reader.close()


def test_moving_the_bubble_while_recording_moves_it_in_the_recording_and_is_remembered(tmp_path, qapp):
    flow, made, *_ = camera_flow(tmp_path, RecordingChoices(webcam=True, countdown=0))
    record_with(flow, flow.panel.choices)
    recorder, bubble = made["recorders"][0], made["bubbles"][0]
    moves = []
    recorder.move_bubble = lambda rect, shape: moves.append((tuple(rect), shape))

    bubble.rect.moveTo(500, 300)
    bubble.moved.emit(bubble.area_rect())

    assert moves == [((500, 300, 192, 192), "circle")]
    assert (video_prefs.load().bubble_x, video_prefs.load().bubble_y) == (500, 300)


def test_the_camera_goes_when_the_recording_ends(tmp_path, qapp):
    flow, made, *_ = camera_flow(tmp_path, RecordingChoices(webcam=True, countdown=0))
    record_with(flow, flow.panel.choices)

    made["recorders"][0].finish(2)

    assert made["webcams"][0].calls[-1] == "stop" and made["bubbles"][0].closed


def test_a_bubble_placed_before_recording_is_remembered(tmp_path, qapp):
    flow, made, *_ = camera_flow(tmp_path, RecordingChoices(webcam=True, countdown=0))
    bubble = made["bubbles"][0]
    bubble.rect.moveTo(700, 400)
    bubble.moved.emit(bubble.area_rect())  # dragged while getting ready
    bubble._shape = "rounded"
    bubble.shape_changed.emit("rounded")

    record_with(flow, flow.panel.choices)  # the panel's copy of the choices predates the drag

    saved = video_prefs.load()
    assert (saved.bubble_x, saved.bubble_y, saved.bubble_shape) == (700, 400, "rounded")


def test_the_camera_isnt_switched_on_by_itself_next_time(tmp_path, qapp):
    flow, made, *_ = camera_flow(tmp_path, RecordingChoices(webcam=True, webcam_id="{cam}", countdown=0))

    record_with(flow, flow.panel.choices)

    assert video_prefs.load().webcam is False and video_prefs.load().webcam_id == "{cam}"


def test_a_camera_that_fails_mid_recording_is_hidden_from_it_at_once(tmp_path, qapp):
    flow, made, *_ = camera_flow(tmp_path, RecordingChoices(webcam=True, countdown=0))
    record_with(flow, flow.panel.choices)
    recorder = made["recorders"][0]
    moves = []
    recorder.move_bubble = lambda rect, shape: moves.append(rect)

    made["webcams"][0].failed.emit("The camera was unplugged")

    assert moves == [None] and made["bubbles"][0].closed


def test_the_recording_follows_the_bubble_while_it_is_dragged(tmp_path, qapp):
    flow, made, *_ = camera_flow(tmp_path, RecordingChoices(webcam=True, countdown=0))
    record_with(flow, flow.panel.choices)
    recorder, bubble = made["recorders"][0], made["bubbles"][0]
    moves = []
    recorder.move_bubble = lambda rect, shape: moves.append(tuple(rect) if rect else None)

    bubble.rect.moveTo(300, 250)  # mid-drag: no "moved" yet
    QTest.qWait(150)

    assert (300, 250, 192, 192) in moves


def test_the_camera_light_goes_off_as_soon_as_recording_stops(tmp_path, qapp):
    flow, made, *_ = camera_flow(tmp_path, RecordingChoices(webcam=True, countdown=0))
    record_with(flow, flow.panel.choices)

    made["recorders"][0].state_changed.emit("finishing")

    assert made["webcams"][0].calls[-1] == "stop"
