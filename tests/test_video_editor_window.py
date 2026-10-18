"""The video editor window (snipforge/video/editor_window.py): a real recording, read through Qt's
FFmpeg (frames, thumbnails, frame times), with real mouse and key events on the timeline. Qt's
player is replaced by a stand-in, so nothing here waits on QMediaPlayer's timing; the desktop
suite plays a real file (tests/desktop/test_video_editor.py)."""
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QEvent, QObject, QPointF, Qt, Signal
from PySide6.QtGui import QImage, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from snipforge.video import clip, editor_parts, editor_window, ffmpeg, frames, gif
from snipforge.video.editor_window import VideoEditorWindow, open_video

pytestmark = pytest.mark.skipif(sys.platform != "win32" and not ffmpeg.available(),
                                reason="Qt's FFmpeg isn't usable here (ffmpeg.unavailable_reason() says why)")
FPS = 30


class FakePlayer(QObject):
    frame_shown = Signal(QImage, float)
    finished = Signal()
    failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple] = []

    def load(self, path) -> None:
        self.calls.append(("load", Path(path)))

    def play(self, at: float) -> None:
        self.calls.append(("play", at))

    def pause(self) -> None:
        self.calls.append(("pause",))

    def close(self) -> None:
        self.calls.append(("close",))


@pytest.fixture(scope="module")
def recording(tmp_path_factory, qapp):
    return clip.make_test_clip(tmp_path_factory.mktemp("rec") / "Recording 1.mp4", seconds=2.0, size=(320, 240),
                               fps=FPS)


@pytest.fixture
def video(recording, tmp_path):
    """Each test gets its own copy, so what it saves next to it is its own."""
    return Path(shutil.copy(recording, tmp_path / "Recording 1.mp4"))


@pytest.fixture
def window(video):
    w = VideoEditorWindow(video, player=FakePlayer())
    w.resize(960, 640)
    w.show()
    QApplication.processEvents()
    yield w
    w.close()


def wait_until(condition, timeout: float = 10.0) -> None:
    end = time.perf_counter() + timeout
    while not condition():
        if time.perf_counter() > end:
            raise AssertionError("timed out waiting")
        QApplication.processEvents()
        time.sleep(0.005)


def shown_number(window) -> int | None:
    image = window.view.image
    return None if image is None or image.isNull() else clip.frame_number(image)


def send(widget, kind: str, x: float, modifiers=Qt.NoModifier) -> None:
    """A real mouse event on the timeline, at `x` and half way down."""
    point = QPointF(x, widget.height() / 2)
    etype = {"press": QEvent.MouseButtonPress, "move": QEvent.MouseMove, "release": QEvent.MouseButtonRelease}[kind]
    button = Qt.NoButton if kind == "move" else Qt.LeftButton
    buttons = Qt.NoButton if kind == "release" else Qt.LeftButton
    QApplication.sendEvent(widget, QMouseEvent(etype, point, widget.mapToGlobal(point), button, buttons, modifiers))


def drag(widget, a: float, b: float) -> None:
    send(widget, "press", a)
    send(widget, "move", (a + b) / 2)
    send(widget, "move", b)
    send(widget, "release", b)


def click(widget, x: float) -> None:
    send(widget, "press", x)
    send(widget, "release", x)


def middle_of(window, index: int) -> float:
    strip = window.timeline
    return (strip.x_for(strip.frames.boundary(index)) + strip.x_for(strip.frames.boundary(index + 1))) / 2


def test_a_recording_opens_with_its_length_and_first_frame(window):
    assert "Recording 1.mp4" in window.windowTitle()
    assert len(window.timeline.frames) == 60
    assert window.time_label.text() == "0:00.00 / 0:02.00"
    assert not window.save_button.isEnabled()  # nothing trimmed yet
    assert window.gif_button.isEnabled()
    wait_until(lambda: shown_number(window) == 0)


def test_thumbnails_fill_the_timeline(window):
    wait_until(lambda: len(window.timeline.thumbnails) >= 5)

    for seconds, image in window.timeline.thumbnails.items():
        assert clip.frame_number(image) == window.timeline.frames.index_at(seconds)


def drag_handle(strip, name: str, to_seconds: float) -> None:
    """Drag a handle by the distance from where its frame boundary is now to `to_seconds` (it keeps
    its place under the pointer rather than jumping to it)."""
    grab = strip.handle_rect(name).center().x()
    now = strip.range.start if name == "in" else strip.range.end
    drag(strip, grab, grab + strip.x_for(to_seconds) - strip.x_for(now))


def test_dragging_the_in_handle_trims_to_a_frame_and_shows_it(window):
    strip = window.timeline
    drag_handle(strip, "in", 0.51)

    assert strip.range.first == 15
    assert window.save_button.isEnabled()
    assert "0:00.50" in window.range_label.text()
    wait_until(lambda: shown_number(window) == 15)


def test_dragging_the_out_handle_shows_the_last_frame_kept(window):
    strip = window.timeline
    drag_handle(strip, "out", 1.49)

    assert strip.range.stop == 45
    wait_until(lambda: shown_number(window) == 44)


def test_clicking_the_timeline_moves_the_playhead(window):
    click(window.timeline, middle_of(window, 30))

    assert window.timeline.playhead == 30
    assert window.time_label.text().startswith("0:01.00")
    wait_until(lambda: shown_number(window) == 30)


def test_arrow_keys_nudge_the_chosen_handle_one_frame(window):
    strip = window.timeline
    click(strip, strip.handle_rect("in").center().x())  # chooses the in handle without moving it
    assert strip.range.first == 0

    QTest.keyClick(strip, Qt.Key_Right)
    assert strip.range.first == 1
    QTest.keyClick(strip, Qt.Key_Right, Qt.ShiftModifier)
    assert strip.range.first == 11
    QTest.keyClick(strip, Qt.Key_Left)
    assert strip.range.first == 10

    click(strip, strip.handle_rect("out").center().x())
    QTest.keyClick(strip, Qt.Key_Left)
    assert strip.range.stop == 59
    wait_until(lambda: shown_number(window) == 58)


def test_arrow_keys_step_the_playhead(window):
    strip = window.timeline
    click(strip, middle_of(window, 20))

    QTest.keyClick(strip, Qt.Key_Right)
    assert strip.playhead == 21
    QTest.keyClick(strip, Qt.Key_Left, Qt.ShiftModifier)
    assert strip.playhead == 11


def test_i_and_o_set_the_range_at_the_playhead(window):
    strip = window.timeline
    click(strip, middle_of(window, 20))
    QTest.keyClick(strip, Qt.Key_I)
    click(strip, middle_of(window, 40))
    QTest.keyClick(strip, Qt.Key_O)

    assert (strip.range.first, strip.range.stop) == (20, 41)  # the frame at the playhead is the last kept


def keep(window, first: int, last: int) -> None:
    click(window.timeline, middle_of(window, first))
    QTest.keyClick(window.timeline, Qt.Key_I)
    click(window.timeline, middle_of(window, last))
    QTest.keyClick(window.timeline, Qt.Key_O)


def test_save_trimmed_copy_writes_a_new_file_next_to_the_original(window, video):
    original = video.read_bytes()
    keep(window, 15, 44)

    window.save_button.click()
    wait_until(lambda: "Saved" in window.status_label.text())
    window.save_button.click()
    wait_until(lambda: "(trimmed 2)" in window.status_label.text())

    first, second = video.with_name("Recording 1 (trimmed).mp4"), video.with_name("Recording 1 (trimmed 2).mp4")
    assert [clip.frame_number(image) for _t, image in frames.read(first)] == list(range(15, 45))
    assert second.is_file()
    assert video.read_bytes() == original
    assert window.reveal_button.isVisible()


def test_export_gif_writes_the_chosen_part(window, video):
    keep(window, 30, 59)

    window.gif_button.click()
    wait_until(lambda: "Saved" in window.status_label.text())

    with Image.open(video.with_suffix(".gif")) as im:
        assert im.n_frames == 12  # 1 s at 12 frames a second
        assert im.size == (320, 240)


def test_export_gif_shows_progress_and_can_be_cancelled(video):
    asked = {}
    started = threading.Event()

    def slow_export(src, dst, start, end, progress=None, cancel=None, **kwargs):
        asked.update(src=Path(src), dst=Path(dst), start=start, end=end)
        progress(0.25)
        started.set()
        while not cancel():
            time.sleep(0.01)
        raise gif.Cancelled

    w = VideoEditorWindow(video, player=FakePlayer(), export_gif=slow_export)
    w.show()
    try:
        keep(w, 6, 59)
        w.gif_button.click()
        wait_until(lambda: started.is_set() and w.progress.value() == 25)
        assert w.cancel_button.isVisible()
        assert not w.gif_button.isEnabled() and not w.save_button.isEnabled()

        w.cancel_button.click()
        wait_until(lambda: "cancelled" in w.status_label.text().lower())
        assert w.gif_button.isEnabled()
        assert not w.progress.isVisible()
        assert asked == {"src": video, "dst": video.with_suffix(".gif"), "start": pytest.approx(0.2), "end": None}
        assert not video.with_suffix(".gif").exists()
    finally:
        w.close()


def test_closing_while_a_gif_is_being_made_asks_first(video, monkeypatch):
    started = threading.Event()

    def slow_export(src, dst, start, end, progress=None, cancel=None, **kwargs):
        started.set()
        while not cancel():
            time.sleep(0.01)
        raise gif.Cancelled

    w = VideoEditorWindow(video, player=FakePlayer(), export_gif=slow_export)
    w.show()
    w.gif_button.click()
    assert started.wait(5)

    assert not w.close()  # the conftest answers No: the GIF carries on
    assert w.isVisible() and w.cancel_button.isVisible()

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    assert w.close()
    assert not w._job.is_running
    assert w.player.calls[-1] == ("close",)


def test_long_videos_get_their_thumbnails_one_jump_at_a_time(video, monkeypatch):
    """Past a length, thumbnails are read by jumping to each place rather than decoding it all."""
    monkeypatch.setattr(editor_parts, "READ_THROUGH_UP_TO", 0.5)
    w = VideoEditorWindow(video, player=FakePlayer())
    w.show()
    try:
        wait_until(lambda: len(w.timeline.thumbnails) == editor_parts.THUMBNAILS)
        for seconds, image in w.timeline.thumbnails.items():
            assert clip.frame_number(image) == w.timeline.frames.index_at(seconds)
    finally:
        w.close()


def test_frames_from_before_where_playing_started_are_not_shown(window):
    click(window.timeline, middle_of(window, 30))
    wait_until(lambda: shown_number(window) == 30)
    window.play_button.click()

    window.player.frame_shown.emit(QImage(8, 8, QImage.Format.Format_ARGB32), 0.0)  # Qt started at the beginning

    assert window.timeline.playhead == 30
    assert shown_number(window) == 30


@pytest.mark.parametrize("taken, label, suffix, expected", [
    ([], "trimmed", ".mp4", "Clip (trimmed).mp4"),
    (["Clip (trimmed).mp4"], "trimmed", ".mp4", "Clip (trimmed 2).mp4"),
    ([], None, ".gif", "Clip.gif"),
    (["Clip.gif", "Clip (2).gif"], None, ".gif", "Clip (3).gif"),
    ([], None, ".mp4", "Clip (2).mp4"),  # never the original itself
])
def test_saved_files_get_names_that_aren_t_taken(tmp_path, taken, label, suffix, expected):
    original = tmp_path / "Clip.mp4"
    original.write_bytes(b"")
    for name in taken:
        (tmp_path / name).write_bytes(b"")

    assert editor_parts.next_free_name(original, label, suffix) == tmp_path / expected


def test_a_failed_save_says_why(video):
    def broken(src, dst, start=0.0, end=None):
        raise OSError("The disk is full")

    w = VideoEditorWindow(video, player=FakePlayer(), trim_copy=broken)
    w.show()
    try:
        keep(w, 1, 10)
        w.save_button.click()
        wait_until(lambda: "disk is full" in w.status_label.text())
        assert w.save_button.isEnabled()
    finally:
        w.close()


def test_play_starts_at_the_playhead_and_stops_at_the_out_point(window):
    player, strip = window.player, window.timeline
    keep(window, 10, 29)
    click(strip, middle_of(window, 12))

    window.play_button.click()
    assert player.calls[-1][0] == "play"
    assert strip.frames.index_at(player.calls[-1][1]) == 12
    assert window.is_playing

    player.frame_shown.emit(QImage(8, 8, QImage.Format.Format_ARGB32), 20 / FPS)
    assert strip.playhead == 20
    player.frame_shown.emit(QImage(8, 8, QImage.Format.Format_ARGB32), 30 / FPS)  # past the out point

    assert player.calls[-1] == ("pause",)
    assert not window.is_playing
    assert strip.playhead == 29


def test_play_from_the_last_frame_starts_again_at_the_in_point(window):
    keep(window, 10, 29)  # leaves the playhead on frame 29

    window.play_button.click()

    assert window.timeline.frames.index_at(window.player.calls[-1][1]) == 10


def test_space_plays_and_pauses(window):
    QTest.keyClick(window.timeline, Qt.Key_Space)
    assert window.is_playing
    QTest.keyClick(window.timeline, Qt.Key_Space)
    assert not window.is_playing
    assert window.player.calls[-1] == ("pause",)


def test_moving_the_playhead_while_playing_pauses(window):
    window.play_button.click()
    click(window.timeline, middle_of(window, 40))

    assert not window.is_playing
    assert window.player.calls[-1] == ("pause",)


def test_when_the_player_fails_trimming_still_works(window):
    window.play_button.click()
    window.player.failed.emit("The video didn't start playing")

    assert not window.is_playing
    assert "didn't start playing" in window.status_label.text()
    keep(window, 3, 9)
    assert window.save_button.isEnabled()


def test_closing_lets_go_of_the_player(window):
    window.close()

    assert window.player.calls[-1] == ("close",)


def test_open_video_shows_a_window(video):
    w = open_video(video)
    try:
        assert w.isVisible()
        assert len(w.timeline.frames) == 60
    finally:
        w.close()


def test_a_file_that_isn_t_a_video_is_refused(tmp_path):
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(b"not a video at all")

    with pytest.raises(ffmpeg.FFmpegError):
        VideoEditorWindow(bad, player=FakePlayer())


def test_running_it_by_hand_needs_one_video(qapp, capsys):
    assert editor_window.main([]) == 2
    assert "Usage" in capsys.readouterr().err


def test_running_it_by_hand_on_a_broken_file_says_so(qapp, tmp_path, _never_block_on_dialogs):
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(b"not a video at all")

    assert editor_window.main([str(bad)]) == 1
    assert _never_block_on_dialogs == ["critical"]
