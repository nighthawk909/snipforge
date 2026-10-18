"""Recording from the app: the tray's Record video, the shortcut, the Record switch in any snip, and a
finished recording opening in the video editor. The recording itself is faked (record_flow has its
own tests)."""
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QRect, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

from tests.test_repeat_snip import forge  # noqa: F401 - the app, as the repeat-snip tests make it


class FakeSession:
    made: list = []

    def __init__(self, on_done, repeat=None, on_note=None, on_area=None, record=False):
        self.on_done, self.on_area, self.record = on_done, on_area, record
        FakeSession.made.append(self)

    def start(self):
        pass

    def snip_now(self):
        pass


class FakeFlow(QObject):
    ended = Signal()
    made: list = []

    def __init__(self, area, screen, folder, *, notify, open_video):
        super().__init__()
        self.area, self.folder, self.open_video, self.calls = area, folder, open_video, []
        FakeFlow.made.append(self)

    def start(self):
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")


@pytest.fixture
def fakes(forge, monkeypatch):  # noqa: F811 - the fixture imported above
    from snipforge import app as app_module
    from snipforge.video import record_flow

    FakeSession.made, FakeFlow.made = [], []
    monkeypatch.setattr(app_module, "CaptureSession", FakeSession)
    monkeypatch.setattr(record_flow, "RecordFlow", FakeFlow)
    return forge


def tray_texts(f):
    return {a.text(): a for a in f._menu.actions()}


def test_the_tray_offers_record_video(fakes):
    actions = tray_texts(fakes)

    assert actions["Record video"].isVisible() and not actions["Stop recording"].isVisible()


def test_record_video_opens_the_overlay_ready_to_record(fakes):
    fakes.record()

    session = FakeSession.made[-1]
    assert session.record and session.on_area is not None and fakes._session is session


def test_every_snip_offers_the_record_switch(fakes):
    fakes.capture()

    assert FakeSession.made[-1].on_area is not None and not FakeSession.made[-1].record


def test_an_area_chosen_to_record_starts_a_recording_saved_in_the_library(fakes):
    fakes._record_area(QRect(10, 20, 640, 360), QGuiApplication.primaryScreen())

    flow = FakeFlow.made[-1]
    assert flow.calls == ["start"] and flow.folder == fakes.library.root
    actions = tray_texts(fakes)
    assert actions["Stop recording"].isVisible() and not actions["Record video"].isVisible()


def test_record_again_or_stop_recording_stops_it(fakes):
    fakes._record_area(QRect(10, 20, 640, 360), QGuiApplication.primaryScreen())
    flow = FakeFlow.made[-1]

    fakes.record()  # the shortcut, pressed again
    tray_texts(fakes)["Stop recording"].trigger()

    assert flow.calls == ["start", "stop", "stop"] and len(FakeSession.made) == 0


def test_when_the_recording_ends_the_tray_offers_record_again(fakes):
    fakes._record_area(QRect(10, 20, 640, 360), QGuiApplication.primaryScreen())

    FakeFlow.made[-1].ended.emit()

    assert fakes._recording is None and tray_texts(fakes)["Record video"].isVisible()


def test_the_record_shortcut_records(fakes):
    fakes._on_shortcut("record")

    assert FakeSession.made[-1].record


def test_quitting_stops_the_recording(fakes):
    fakes._record_area(QRect(10, 20, 640, 360), QGuiApplication.primaryScreen())

    fakes._stop_recording_on_quit()

    assert FakeFlow.made[-1].calls[-1] == "stop"


def test_a_finished_recording_opens_in_the_video_editor(fakes, monkeypatch):
    from snipforge.video import editor_window

    opened = []

    def open_video(path):
        opened.append(Path(path))
        return QWidget()

    monkeypatch.setattr(editor_window, "open_video", open_video)
    fakes._record_area(QRect(10, 20, 640, 360), QGuiApplication.primaryScreen())

    FakeFlow.made[-1].open_video(Path("C:/rec.mp4"))

    assert opened == [Path("C:/rec.mp4")] and any(isinstance(w, QWidget) for w in fakes._windows)


def test_no_second_recording_while_one_is_under_way(fakes):
    fakes._record_area(QRect(10, 20, 640, 360), QGuiApplication.primaryScreen())

    fakes.capture()  # the snip hotkey during a recording
    assert FakeSession.made[-1].on_area is None  # no Record switch in that snip
    fakes._record_area(QRect(0, 0, 320, 240), QGuiApplication.primaryScreen())  # however it got there

    assert len(FakeFlow.made) == 1 and "already" in fakes.messages[-1]


def test_a_recording_that_fails_to_start_leaves_nothing_behind(fakes, monkeypatch):
    def broken_start(self):
        raise RuntimeError("no panel today")

    monkeypatch.setattr(FakeFlow, "start", broken_start)
    monkeypatch.setattr(FakeFlow, "_end", lambda self, note: (fakes.messages.append(note), self.ended.emit()),
                        raising=False)

    fakes._record_area(QRect(10, 20, 640, 360), QGuiApplication.primaryScreen())

    assert fakes._recording is None and tray_texts(fakes)["Record video"].isVisible()
    assert "couldn't start" in fakes.messages[-1]


def test_a_recording_opens_in_the_video_editor_from_the_library(forge, monkeypatch, qapp):  # noqa: F811
    from snipforge.video import clip, recordings

    video = recordings.new_recording(forge.library.root)
    clip.make_test_clip(video, seconds=0.5)
    recordings.write_details(video, 0.5, (320, 240))
    opened = []
    monkeypatch.setattr(forge, "_open_video", lambda path: opened.append(Path(path)))

    forge.open_entry(video.parent.name)

    assert opened == [video]
