"""The video editor, part two: choose part of the timeline and cut it out of the middle (put it back,
undo, redo), a title card and a caption, playing past a cut, and saving the edit (encoded again)."""
import shutil
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snipforge.video import clip, edit_dialogs, ffmpeg, frames
from snipforge.video.edit import Title
from snipforge.video.editor_window import VideoEditorWindow
from tests.test_video_editor_window import FakePlayer, middle_of, send, wait_until

pytestmark = pytest.mark.skipif(sys.platform != "win32" and not ffmpeg.available(),
                                reason="Qt's FFmpeg isn't usable here (ffmpeg.unavailable_reason() says why)")


@pytest.fixture(scope="module")
def recording(tmp_path_factory, qapp):
    return clip.make_test_clip(tmp_path_factory.mktemp("rec") / "Recording 1.mp4", seconds=2.0, size=(320, 240),
                               fps=30)


@pytest.fixture
def video(recording, tmp_path):
    return Path(shutil.copy(recording, tmp_path / "Recording 1.mp4"))


@pytest.fixture
def window(video):
    w = VideoEditorWindow(video, player=FakePlayer())
    w.resize(960, 640)
    w.show()
    QApplication.processEvents()
    yield w
    w.close()


def active(w):
    w.activateWindow()
    QTest.qWaitForWindowActive(w)


def select(window, first: int, last: int) -> None:
    """Shift+drag across the timeline, from frame `first` to frame `last`."""
    strip = window.timeline
    send(strip, "press", middle_of(window, first), Qt.ShiftModifier)
    send(strip, "move", middle_of(window, (first + last) // 2), Qt.ShiftModifier)
    send(strip, "move", middle_of(window, last), Qt.ShiftModifier)
    send(strip, "release", middle_of(window, last), Qt.ShiftModifier)


def test_shift_dragging_chooses_a_part_and_cut_out_removes_it(window):
    select(window, 20, 29)
    assert window.timeline.selection == (20, 30) and window.cut_button.isEnabled()
    window.cut_button.click()
    assert window.edit.cuts == ((20, 30),) and window.timeline.cuts == ((20, 30),)
    assert window.timeline.selection is None and not window.cut_button.isEnabled()
    assert window.save_button.isEnabled() and "2 parts" in window.range_label.text()
    assert "1:" not in window.range_label.text() and "0:01.67" in window.range_label.text()  # 50 of 60 frames


def test_the_keys_choose_cut_put_back_undo_and_redo(window):
    active(window)
    strip = window.timeline
    strip.setFocus()
    strip.set_playhead(10)
    QTest.keyClick(strip, Qt.Key_BracketLeft)
    strip.set_playhead(19)
    QTest.keyClick(strip, Qt.Key_BracketRight)
    assert strip.selection == (10, 20)
    QTest.keyClick(strip, Qt.Key_X)
    assert window.edit.cuts == ((10, 20),)
    strip.set_playhead(15)  # in the cut: Delete puts it back
    QTest.keyClick(strip, Qt.Key_Delete)
    assert window.edit.cuts == ()
    QTest.keyClick(strip, Qt.Key_Z, Qt.ControlModifier)
    assert window.edit.cuts == ((10, 20),)
    QTest.keyClick(strip, Qt.Key_Z, Qt.ControlModifier)
    assert window.edit.cuts == () and window.timeline.cuts == ()
    QTest.keyClick(strip, Qt.Key_Y, Qt.ControlModifier)
    assert window.edit.cuts == ((10, 20),)
    QTest.keyClick(strip, Qt.Key_Escape)  # Esc forgets a choice
    strip.set_playhead(40)
    QTest.keyClick(strip, Qt.Key_BracketLeft)
    QTest.keyClick(strip, Qt.Key_Escape)
    assert strip.selection is None


def test_a_cut_can_never_take_everything(window):
    window.timeline.set_selection((0, 60))
    window.cut_button.click()
    assert window.edit.cuts == () and "everything" in window.status_label.text()


def test_dragging_a_trim_handle_is_one_undo_step(window):
    from tests.test_video_editor_window import drag_handle

    drag_handle(window.timeline, "in", 0.51)
    assert window.edit.range.first == 15
    window.undo()
    assert window.edit.range.first == 0 and window.timeline.range.first == 0


def test_playing_jumps_over_a_cut(window):
    window.timeline.set_selection((10, 30))
    window.cut_button.click()
    window.toggle_play()
    player = window.player
    window._on_player_frame(clip_frame(5), 5.5 / 30)
    window._on_player_frame(clip_frame(10), 10.5 / 30)  # the first frame cut out: skip to frame 30
    assert player.calls[-1] == ("play", pytest.approx(30.5 / 30))
    assert window.timeline.playhead == 30 and window.is_playing
    window._on_player_frame(clip_frame(12), 12.5 / 30)  # the player's late frames from before the jump
    assert window.timeline.playhead == 30


def clip_frame(n):
    from PySide6.QtGui import QColor, QImage

    image = QImage(320, 240, QImage.Format_RGB32)
    image.fill(QColor("#123456"))
    return image


def test_a_title_and_a_caption_from_their_dialogs(window, monkeypatch):
    monkeypatch.setattr(edit_dialogs, "ask_title", lambda parent, current, size: ("set", Title("Quarterly report", 2)))
    window.title_button.click()
    assert window.edit.title == Title("Quarterly report", 2) and "title" in window.range_label.text()
    monkeypatch.setattr(edit_dialogs, "ask_caption", lambda parent, current: ("set", "Click Save"))
    window.caption_button.click()
    assert window.edit.caption == "Click Save"
    wait_until(lambda: window.view.image is not None)
    window._on_still(0, clip_frame(0))
    assert window.view.image.pixelColor(160, 236).lightness() < 60  # the caption, shown over the frame
    monkeypatch.setattr(edit_dialogs, "ask_caption", lambda parent, current: ("remove", None))
    window.caption_button.click()
    assert window.edit.caption == ""
    monkeypatch.setattr(edit_dialogs, "ask_title", lambda parent, current, size: None)  # cancelled
    window.title_button.click()
    assert window.edit.title == Title("Quarterly report", 2)
    window.undo()
    window.undo()
    assert window.edit.title == Title("Quarterly report", 2) and window.edit.caption == ""


def test_the_title_dialog_shows_its_card_and_checks_its_words(qapp):
    from PySide6.QtCore import QSize

    dialog = edit_dialogs.TitleDialog(None, QSize(320, 240))
    assert not dialog.ok_button.isEnabled()  # no words yet
    dialog.text.setText("Quarterly report")
    dialog.seconds.setValue(4)
    assert dialog.ok_button.isEnabled() and dialog.result_title() == Title("Quarterly report", 4)
    assert not dialog.preview.pixmap().isNull()
    assert dialog.remove_button.isHidden()  # nothing to remove yet
    again = edit_dialogs.TitleDialog(Title("Old", 2), QSize(320, 240))
    assert again.text.text() == "Old" and not again.remove_button.isHidden()


def test_saving_an_edit_encodes_it_again_and_keeps_the_original(window, video):
    window.timeline.set_selection((20, 40))
    window.cut_button.click()
    assert window.save_button.text() == "Save video"
    window.save_button.click()
    wait_until(lambda: window._job is None, timeout=60)
    saved = video.with_name("Recording 1 (edited).mp4")
    assert saved.exists() and "Saved" in window.status_label.text()
    assert [clip.frame_number(image) for _t, image in frames.read(saved)] == list(range(20)) + list(range(40, 60))
    assert video.exists()


def test_closing_while_saving_an_edit_asks_then_stops_it(video, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    w = VideoEditorWindow(video, player=FakePlayer())
    w.show()
    w.timeline.set_selection((10, 20))
    w.cut_button.click()
    w.save_button.click()
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.Yes)
    w.close()
    assert asked and "isn't saved yet" in asked[0]
    wait_until(lambda: not list(video.parent.glob("*.partial.*")))  # Windows lets go of it a moment later
    assert not video.with_name("Recording 1 (edited).mp4").exists()
    QApplication.processEvents()
