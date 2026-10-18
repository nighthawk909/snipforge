"""Getting ready to record: what's remembered (video_prefs), where things go on screen (geometry), where
recordings are saved (recordings), the level preview, and the ready panel."""
import array
import threading

import pytest
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snipforge.settings import app_settings
from snipforge.video import geometry, recordings, video_prefs
from snipforge.video.levels import LevelPreview
from snipforge.video.ready_panel import ReadyPanel
from snipforge.video.video_prefs import RecordingChoices
from snipforge.video.wasapi import Device


@pytest.fixture(autouse=True)
def clean_prefs():
    app_settings().remove("video")
    yield
    app_settings().remove("video")


# --- what's remembered --------------------------------------------------------------------------

def test_choices_are_remembered():
    assert video_prefs.load() == RecordingChoices()  # the defaults: both sounds, every pointer effect, 3 s
    chosen = RecordingChoices(microphone=False, microphone_id="{mic}", system_audio=True, pointer=True,
                              highlight=False, clicks=False, countdown=10)

    video_prefs.save(chosen)

    assert video_prefs.load() == chosen


def test_damaged_choices_fall_back_to_the_defaults():
    settings = app_settings()
    settings.setValue("video/microphone", "maybe")
    settings.setValue("video/countdown", 7)  # not one the menu offers
    settings.setValue("video/microphone_id", "x" * 600)

    assert video_prefs.load() == RecordingChoices()


# --- where things go ------------------------------------------------------------------------------

def test_physical_and_logical_agree_at_100_percent(qapp):
    screen = QGuiApplication.primaryScreen()
    if screen.devicePixelRatio() != 1:
        pytest.skip("this test's screen is scaled")
    area = QRect(screen.geometry().topLeft() + QPoint(40, 50), QSize(320, 240))

    assert geometry.to_logical(area) == area and geometry.screen_of(area) is screen


def test_the_panel_goes_below_the_area_or_above_it_or_inside_it():
    bounds, size = QRect(0, 0, 1000, 800), QSize(300, 40)

    assert geometry.place_beside(QRect(100, 100, 400, 300), size, bounds) == QPoint(150, 410)  # below
    assert geometry.place_beside(QRect(100, 500, 400, 290), size, bounds) == QPoint(150, 450)  # above
    inside = geometry.place_beside(QRect(0, 0, 1000, 800), size, bounds)
    assert QRect(inside, size).bottom() <= 790  # a full-screen area: inside, at the bottom


# --- where recordings are saved -------------------------------------------------------------------

def test_each_recording_gets_a_folder_of_its_own(tmp_path):
    first, second = recordings.new_recording(tmp_path), recordings.new_recording(tmp_path)

    assert first.name == second.name == "recording.mp4"
    assert first.parent != second.parent and first.parent.is_dir() and not first.exists()


@pytest.mark.parametrize("seconds, text", [(0, "0:00"), (7.4, "0:07"), (272, "4:32"), (3729, "1:02:09")])
def test_lengths_read_as_minutes_and_seconds(seconds, text):
    assert recordings.duration_text(seconds) == text


# --- the level preview ----------------------------------------------------------------------------

class FakeCapture:
    """A sound source: `play(level)` sends a packet as loud as `level`."""

    made: list = []

    def __init__(self, kind, device_id, on_packet, on_error, refuse=None):
        self.kind, self.device_id, self.on_packet, self.refuse = kind, device_id, on_packet, refuse
        self.running = False
        FakeCapture.made.append(self)

    def start(self):
        if self.refuse:
            raise OSError(self.refuse)
        self.running = True

    def stop(self):
        self.running = False

    def play(self, level):  # on a thread of its own, as WASAPI's packets arrive
        samples = array.array("f", [level, -level / 2] * 480)
        sender = threading.Thread(target=self.on_packet, args=(samples.tobytes(), 480, None, False))
        sender.start()
        sender.join()


def fake_captures(refuse_microphone=None):
    FakeCapture.made = []

    def make(kind, device_id, on_packet, on_error):
        refuse = refuse_microphone if kind == "microphone" else None
        return FakeCapture(kind, device_id, on_packet, on_error, refuse)
    return make


def test_levels_are_the_loudest_moment_since_last_asked():
    preview = LevelPreview(fake_captures())
    preview.set_source("microphone", True, "{mic}")
    mic = FakeCapture.made[-1]

    mic.play(0.3)
    mic.play(0.6)
    mic.play(0.2)

    assert preview.levels() == {"microphone": pytest.approx(0.6), "system": None}  # system isn't open
    assert preview.levels()["microphone"] == 0.0  # nothing since
    assert mic.device_id == "{mic}"


def test_a_source_switched_off_is_closed():
    preview = LevelPreview(fake_captures())
    preview.set_source("system", True)
    system = FakeCapture.made[-1]

    preview.set_source("system", False)

    assert not system.running and preview.levels()["system"] is None


def test_a_microphone_that_cant_be_opened_says_why():
    preview = LevelPreview(fake_captures(refuse_microphone="Windows' privacy settings block the microphone"))

    preview.set_source("microphone", True)

    assert preview.problems == {"microphone": "Windows' privacy settings block the microphone"}
    assert preview.levels()["microphone"] is None


# --- the ready panel ------------------------------------------------------------------------------

MICS = [Device("{usb}", "USB microphone", False), Device("{array}", "Microphone array", True)]


def make_panel(choices=None, refuse_microphone=None):
    preview = LevelPreview(fake_captures(refuse_microphone))
    panel = ReadyPanel(QRect(100, 100, 400, 300), QGuiApplication.primaryScreen(), choices or RecordingChoices(),
                       preview, devices=lambda: MICS)
    panel.got = []
    panel.record.connect(panel.got.append)
    panel.cancelled.connect(lambda: panel.got.append("cancel"))
    return panel, preview


def test_the_panel_shows_the_remembered_choices_and_listens_to_them(qapp):
    panel, preview = make_panel(RecordingChoices(microphone=True, system_audio=False, countdown=5))

    assert panel.mic_button.isChecked() and not panel.system_button.isChecked()
    assert panel.countdown_actions[5].isChecked() and "5 s" in panel.countdown_button.text()
    assert {c.kind for c in FakeCapture.made if c.running} == {"microphone"}
    panel.cancel()


def test_switching_sources_changes_what_is_listened_to_and_recorded(qapp):
    panel, preview = make_panel()

    QTest.mouseClick(panel.system_button, Qt.LeftButton)

    assert not panel.choices.system_audio
    assert {c.kind for c in FakeCapture.made if c.running} == {"microphone"}
    panel.cancel()


def test_picking_a_microphone_switches_it_on_and_listens_to_that_one(qapp):
    panel, preview = make_panel(RecordingChoices(microphone=False))

    panel.mic_menu.aboutToShow.emit()
    names = [a.text() for a in panel.mic_menu.actions()]
    next(a for a in panel.mic_menu.actions() if a.text() == "USB microphone").trigger()

    assert names == ["Windows' default microphone", "USB microphone", "Microphone array"]
    assert panel.choices.microphone and panel.choices.microphone_id == "{usb}" and panel.mic_button.isChecked()
    assert [c.device_id for c in FakeCapture.made if c.running and c.kind == "microphone"] == ["{usb}"]
    panel.cancel()


def test_pointer_effects_and_countdown_are_chosen_from_menus(qapp):
    panel, _preview = make_panel()

    panel.pointer_actions["clicks"].trigger()
    panel.countdown_actions[0].trigger()

    assert not panel.choices.clicks and panel.choices.countdown == 0
    assert "No countdown" in panel.countdown_button.text()
    panel.cancel()


def test_enter_records_with_the_choices_and_lets_go_of_the_sound(qapp):
    panel, _preview = make_panel()
    panel.show()

    QApplication.sendEvent(panel, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))

    assert panel.got == [RecordingChoices()]
    assert not any(c.running for c in FakeCapture.made)  # the recorder opens them itself


def test_esc_cancels(qapp):
    panel, _preview = make_panel()
    panel.show()

    QApplication.sendEvent(panel, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))

    assert panel.got == ["cancel"]


def test_a_blocked_microphone_is_explained_on_the_panel(qapp):
    panel, _preview = make_panel(refuse_microphone="Windows' privacy settings block the microphone")

    assert not panel.problem.isHidden()
    assert panel.problem.text() == "Microphone: Windows' privacy settings block the microphone"
    panel.cancel()


def test_changing_one_source_leaves_the_other_open():
    preview = LevelPreview(fake_captures())
    preview.set_source("microphone", True)
    preview.set_source("system", True)
    mic = FakeCapture.made[0]

    preview.set_source("system", False)
    preview.set_source("microphone", True)  # unchanged: not reopened

    assert mic.running and len([c for c in FakeCapture.made if c.kind == "microphone"]) == 1


def test_closing_the_panel_any_other_way_cancels(qapp):
    panel, _preview = make_panel()
    panel.show()

    panel.close()  # Alt+F4, or anything else that closes it

    assert panel.got == ["cancel"] and not any(c.running for c in FakeCapture.made)


def test_enter_on_a_focused_button_presses_that_button_not_record(qapp):
    panel, _preview = make_panel()
    panel.show()
    panel.cancel_button.setFocus()

    QApplication.sendEvent(panel.cancel_button, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))

    assert panel.got == ["cancel"]


class Screen:
    """A monitor at 150%, left of the main one: Qt's numbers for it."""

    def __init__(self, x=-1920, y=0, width=1280, height=720, ratio=1.5):
        self._g, self._ratio = QRect(x, y, width, height), ratio

    def geometry(self):
        return self._g

    def devicePixelRatio(self):
        return self._ratio


def test_physical_and_logical_on_a_scaled_monitor_left_of_the_main_one():
    screen = Screen()
    physical = QRect(-1920 + 300, 150, 960, 540)  # physical pixels, 300 in from its left edge

    logical = geometry.to_logical(physical, screen)

    assert logical == QRect(-1920 + 200, 100, 640, 360)  # top-left stays put; inside, 1.5x smaller
    assert geometry.physical_geometry(screen) == QRect(-1920, 0, 1920, 1080)
