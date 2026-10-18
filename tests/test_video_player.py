"""The video editor's player (snipforge/video/player.py): Qt's QMediaPlayer drawn into a QVideoSink,
watched so a stall gets the file loaded again. Qt's player now and then loads a fresh file with no
tracks, or never shows a first frame (docs/VIDEO-PLAN.md, Risks), so these tests drive the player's
logic with a stand-in for QMediaPlayer and decide themselves when frames arrive."""
import os
import time

import pytest
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QColor, QImage
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame
from PySide6.QtWidgets import QApplication

from snipforge.video.player import Player


class FakeMedia(QObject):
    """Stands in for QMediaPlayer and its QVideoSink, recording what it's asked to do."""

    videoFrameChanged = Signal(QVideoFrame)
    mediaStatusChanged = Signal(object)
    errorOccurred = Signal(object, str)

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple] = []
        self.has_video = True
        self.status = QMediaPlayer.MediaStatus.LoadedMedia  # loads at once unless a test says otherwise

    def setSource(self, url: QUrl) -> None:  # noqa: N802 - Qt API
        self.calls.append(("source", url.toLocalFile()))

    def mediaStatus(self):  # noqa: N802 - Qt API
        return self.status

    def setPosition(self, ms: int) -> None:  # noqa: N802 - Qt API
        self.calls.append(("position", ms))

    def play(self) -> None:
        self.calls.append(("play",))

    def pause(self) -> None:
        self.calls.append(("pause",))

    def stop(self) -> None:
        self.calls.append(("stop",))

    def hasVideo(self) -> bool:  # noqa: N802 - Qt API
        return self.has_video

    def show(self, seconds: float, colour: str = "red") -> QImage:
        image = QImage(32, 24, QImage.Format.Format_ARGB32)
        image.fill(QColor(colour))
        frame = QVideoFrame(image)
        frame.setStartTime(round(seconds * 1_000_000))
        self.videoFrameChanged.emit(frame)
        return image


@pytest.fixture
def media(qapp):
    return FakeMedia()


@pytest.fixture
def player(media, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")
    p = Player(make_backend=lambda: (media, media), first_frame_timeout_ms=30, retries=2)
    p.load(video)
    yield p
    p.close()


def caught(signal) -> list:
    got = []
    signal.connect(lambda *args: got.append(args))
    return got


def pump(seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def pump_until(condition, seconds: float = 5.0) -> bool:
    """Run Qt's events until `condition()` (a busy machine's timers come late), up to `seconds`."""
    end = time.perf_counter() + seconds
    while not condition() and time.perf_counter() < end:
        QApplication.processEvents()
        time.sleep(0.005)
    return condition()


def test_nothing_is_opened_until_the_first_play(qapp, tmp_path):
    made = []
    p = Player(make_backend=lambda: made.append(1) or (FakeMedia(), FakeMedia()))
    p.load(tmp_path / "clip.mp4")

    assert made == []
    p.close()


def test_playing_seeks_and_passes_on_frames_with_their_times(player, media):
    shown = caught(player.frame_shown)

    player.play(1.25)
    image = media.show(1.25)

    assert media.calls[-3:] == [("source", media.calls[0][1]), ("position", 1250), ("play",)]
    assert media.calls[0][1].endswith("clip.mp4")
    assert len(shown) == 1
    assert shown[0][0].pixelColor(1, 1) == image.pixelColor(1, 1)
    assert shown[0][1] == pytest.approx(1.25)
    assert player.is_playing


def test_a_file_still_loading_is_played_from_the_right_place_once_loaded(player, media):
    """Qt's player drops a seek made before the file has loaded and plays from the start, so the
    seek waits for the file."""
    media.status = QMediaPlayer.MediaStatus.LoadingMedia
    player.play(1.5)
    assert [c for c in media.calls if c[0] != "source"] == []

    media.status = QMediaPlayer.MediaStatus.LoadedMedia
    media.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.LoadedMedia)

    assert media.calls[-2:] == [("position", 1500), ("play",)]


def test_frames_after_a_pause_are_ignored(player, media):
    shown = caught(player.frame_shown)
    player.play(0.0)
    player.pause()

    media.show(0.1)

    assert shown == []
    assert media.calls[-1] == ("pause",)
    assert not player.is_playing


def test_the_empty_frame_at_the_end_is_ignored(player, media):
    shown = caught(player.frame_shown)
    player.play(0.0)

    media.videoFrameChanged.emit(QVideoFrame())

    assert shown == []


def test_a_start_that_stalls_loads_the_file_again(player, media):
    player.play(0.5)
    assert pump_until(lambda: len([c for c in media.calls if c[0] == "source"]) >= 3)  # after its 30 ms wait

    sources = [c for c in media.calls if c[0] == "source"]
    assert [s[1] for s in sources[1:3]] == ["", sources[0][1]]  # let go of the file, then opened again
    assert media.calls.count(("position", 500)) >= 2
    assert media.calls.count(("play",)) >= 2


def test_a_file_loaded_without_tracks_is_loaded_again(player, media):
    media.has_video = False
    player.play(0.0)
    plays = media.calls.count(("play",))

    media.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.LoadedMedia)

    assert media.calls.count(("play",)) == plays + 1
    assert ("source", "") in media.calls


def test_playing_resumes_from_the_last_frame_shown_after_a_reload(player, media):
    player.play(0.0)
    media.show(0.4)
    media.errorOccurred.emit(QMediaPlayer.Error.ResourceError, "decoder hiccup")

    assert media.calls[-2:] == [("position", 400), ("play",)]


def test_it_gives_up_after_a_few_tries_and_says_so(player, media):
    failed = caught(player.failed)
    player.play(0.0)
    assert pump_until(lambda: failed)
    pump(0.1)  # and nothing more after that

    assert len(failed) == 1
    assert "play" in failed[0][0].lower()
    assert not player.is_playing
    assert media.calls.count(("play",)) == 3  # the first try and two more


def test_once_frames_arrive_the_watchdog_rests(player, media):
    failed = caught(player.failed)
    player.play(0.0)
    media.show(0.0)
    pump(0.15)

    assert failed == []
    assert media.calls.count(("play",)) == 1


def test_the_end_of_the_video_is_reported(player, media):
    finished = caught(player.finished)
    player.play(0.0)
    media.show(0.0)

    media.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)

    assert len(finished) == 1
    assert not player.is_playing


def test_only_local_files_are_played(qapp):
    p = Player(make_backend=lambda: (FakeMedia(), FakeMedia()))
    with pytest.raises(ValueError):
        p.load("http://127.0.0.1:9/clip.mp4")


def test_qt_s_player_is_limited_to_local_files(qapp):
    import snipforge.video  # noqa: F401 - sets it before Qt Multimedia loads

    assert os.environ["QT_FFMPEG_PROTOCOL_WHITELIST"] == "file"
