"""snipforge/video/webcam.py: the camera, for the bubble on screen and for recorded frames.

A fake camera stands in for Qt's (QCamera, QVideoSink) so no test switches a real camera on; the
desktop suite (tests/desktop/test_video_camera.py) opens the real one.
"""
import subprocess
import sys
import threading

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest

from snipforge.video import webcam


def picture(colour: str = "#ff0000", size: tuple[int, int] = (64, 48)) -> QImage:
    image = QImage(*size, QImage.Format_RGBA8888_Premultiplied)  # what Qt's toImage() gives
    image.fill(QColor(colour))
    return image


class FakeVideoFrame:
    """A QVideoFrame: converting it is what costs, so conversions are counted."""

    def __init__(self, image: QImage):
        self.image = image
        self.conversions = 0

    def toImage(self):
        self.conversions += 1
        return self.image


class FakeSession:
    def __init__(self, device_id, on_frame, on_error, fail_on_start=None):
        self.device_id, self.on_frame, self.on_error = device_id, on_frame, on_error
        self.fail_on_start = fail_on_start
        self.started = self.closed = False

    def start(self):
        self.started = True
        if self.fail_on_start is not None:
            self.on_error(self.fail_on_start)  # Qt can report a failure from inside start()

    def close(self):
        self.closed = True


class FakeBackend:
    def __init__(self, devices=(("cam-1", "Front camera"), ("cam-2", "USB camera")), blocked=False):
        self.listed = list(devices)
        self.is_blocked = blocked
        self.sessions = []
        self.open_error = None
        self.fail_on_start = None

    def devices(self):
        return list(self.listed)

    def blocked(self):
        return self.is_blocked

    def open(self, device_id, on_frame, on_error, parent):
        if self.open_error is not None:
            raise self.open_error
        session = FakeSession(device_id, on_frame, on_error, self.fail_on_start)
        self.sessions.append(session)
        return session

    @property
    def session(self):
        return self.sessions[-1]


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


@pytest.fixture
def backend():
    return FakeBackend()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def camera(qapp, backend, clock):
    cam = webcam.Webcam(backend=backend, now=clock)
    cam.failures = []
    cam.failed.connect(cam.failures.append)
    cam.pictures_shown = []
    cam.frame.connect(cam.pictures_shown.append)
    yield cam
    cam.stop()


# Choosing a camera


def test_devices_lists_each_camera_by_id_and_name(camera):
    assert camera.devices() == [("cam-1", "Front camera"), ("cam-2", "USB camera")]


def test_no_camera_at_all_is_explained_and_nothing_is_opened(qapp):
    backend = FakeBackend(devices=())
    cam = webcam.Webcam(backend=backend)
    failures = []
    cam.failed.connect(failures.append)

    assert cam.start() is False

    assert failures == [webcam.NO_CAMERA] and "No camera" in failures[0]
    assert backend.sessions == [] and cam.latest() is None and not cam.is_active


def test_the_default_camera_is_the_first_listed(camera, backend):
    assert camera.start() is True
    assert backend.session.device_id == "cam-1" and backend.session.started
    assert camera.is_active and camera.device_id == "cam-1"


def test_a_chosen_camera_is_opened(camera, backend):
    assert camera.start("cam-2") is True
    assert backend.session.device_id == "cam-2"


def test_a_chosen_camera_that_isnt_plugged_in_is_explained(camera, backend):
    assert camera.start("cam-9") is False
    assert camera.failures == [webcam.CHOSEN_MISSING] and backend.sessions == []


def test_windows_privacy_setting_is_explained_without_switching_the_camera_on(camera, backend):
    backend.is_blocked = True

    assert camera.start() is False

    assert camera.failures == [webcam.blocked_message()] and "Privacy" in camera.failures[0]
    assert backend.sessions == []


def test_cameras_that_cant_be_listed_are_explained(camera, backend):
    def broken():
        raise OSError("Media Foundation is missing")

    backend.devices = broken
    assert camera.devices() == []
    assert camera.start() is False
    assert camera.failures == [f"{webcam.LIST_FAILED} (Media Foundation is missing)."]


def test_starting_again_closes_the_camera_that_was_open(camera, backend):
    camera.start("cam-1")
    first = backend.session
    camera.start("cam-2")
    assert first.closed and not backend.session.closed


# Pictures


def test_a_picture_is_kept_as_an_opaque_copy_and_shown(camera, backend):
    camera.start()
    backend.session.on_frame(FakeVideoFrame(picture("#ff0000")))

    latest = camera.latest()
    assert latest.format() == QImage.Format_RGB32  # the quickest kind to draw into recorded frames
    assert latest.size() == picture().size() and latest.pixelColor(5, 5) == QColor("#ff0000")
    assert len(camera.pictures_shown) == 1 and camera.pictures_shown[0] == latest


def test_pictures_are_converted_at_most_about_30_times_a_second(camera, backend, clock):
    camera.start()
    frames = []
    for t in (0.0, 0.010, 0.020, 0.034, 0.040, 0.070, 0.080):  # a 60-100 fps camera
        clock.t = 100.0 + t
        frame = FakeVideoFrame(picture())
        frames.append(frame)
        backend.session.on_frame(frame)

    assert [f.conversions for f in frames] == [1, 0, 0, 1, 0, 1, 0]  # skipped ones cost nothing
    assert len(camera.pictures_shown) == 3


def test_a_30_fps_camera_keeps_every_picture_despite_jitter(camera, backend, clock):
    camera.start()
    for t in (0.0, 0.031, 0.066, 0.097, 0.131):
        clock.t = 100.0 + t
        backend.session.on_frame(FakeVideoFrame(picture()))
    assert len(camera.pictures_shown) == 5


def test_an_empty_picture_is_ignored(camera, backend):
    camera.start()
    backend.session.on_frame(FakeVideoFrame(QImage()))
    assert camera.latest() is None and camera.pictures_shown == []


def test_the_latest_picture_can_be_read_from_another_thread(camera, backend):
    camera.start()
    backend.session.on_frame(FakeVideoFrame(picture("#00ff00")))
    seen = []
    reader = threading.Thread(target=lambda: seen.append(camera.latest().pixelColor(1, 1)))
    reader.start()
    reader.join(5)
    assert seen == [QColor("#00ff00")]


# Stopping and failing


def test_stopped_is_said_only_when_a_camera_was_on(camera, backend):
    stops = []
    camera.stopped.connect(lambda: stops.append("stopped"))
    camera.stop()
    assert stops == []
    camera.start()
    camera.stop()
    assert stops == ["stopped"]


def test_a_failure_says_stopped_before_failed(camera, backend):
    said = []
    camera.stopped.connect(lambda: said.append("stopped"))
    camera.failed.connect(lambda message: said.append("failed"))
    camera.start()
    backend.session.on_error("Device lost")
    assert said == ["stopped", "failed"]


def test_the_wait_for_a_first_picture_starts_once_the_camera_has(camera, backend, monkeypatch):
    """A camera slow to start mustn't use up the time it has to send its first picture."""
    watching = []
    real_start = FakeSession.start
    monkeypatch.setattr(FakeSession, "start", lambda self: watching.append(camera.waiting_for_picture)
                        or real_start(self))
    camera.start()
    assert watching == [False] and camera.waiting_for_picture


def test_closing_a_camera_that_fails_to_close_is_logged_not_raised(camera, backend, caplog):
    camera.start()
    backend.session.close = lambda: (_ for _ in ()).throw(RuntimeError("already deleted"))
    camera.stop()
    assert not camera.is_active and "Closing the camera failed" in caplog.text


def test_stop_closes_the_camera_and_forgets_its_picture(camera, backend):
    camera.start()
    backend.session.on_frame(FakeVideoFrame(picture()))
    session = backend.session

    camera.stop()
    session.on_frame(FakeVideoFrame(picture()))  # one still on its way
    session.on_error("")  # Qt reports the camera going inactive after stop(): not a failure

    assert session.closed and camera.latest() is None and not camera.is_active
    assert camera.failures == [] and len(camera.pictures_shown) == 1


def test_a_camera_that_fails_while_starting_is_explained(camera, backend):
    backend.fail_on_start = "Failed to create camera IMFMediaSource"

    assert camera.start() is False

    assert len(camera.failures) == 1 and "couldn't start" in camera.failures[0]
    assert "IMFMediaSource" in camera.failures[0] and backend.session.closed


def test_a_camera_that_cant_be_opened_is_explained(camera, backend):
    backend.open_error = RuntimeError("no such device")
    assert camera.start() is False
    assert len(camera.failures) == 1 and "no such device" in camera.failures[0]


def test_a_failure_is_reported_once_and_closes_the_camera(camera, backend):
    camera.start()
    backend.session.on_frame(FakeVideoFrame(picture()))
    backend.session.on_error("Device lost")
    backend.session.on_error("Device lost")

    assert len(camera.failures) == 1 and "stopped working" in camera.failures[0]
    assert backend.session.closed and camera.latest() is None


def test_an_unplugged_camera_is_explained(camera, backend):
    camera.start()
    backend.session.on_frame(FakeVideoFrame(picture()))
    backend.listed = [("cam-2", "USB camera")]
    backend.session.on_error("")
    assert camera.failures == [webcam.CAMERA_GONE]


def test_a_camera_in_use_is_explained(camera, backend):
    camera.start()
    backend.session.on_error("The video recording device is in use by another process")
    assert camera.failures == [webcam.IN_USE]


def test_a_privacy_block_found_on_failure_is_explained(camera, backend):
    camera.start()
    backend.is_blocked = True  # switched off in Settings while the camera was starting
    backend.session.on_error("Failed to create camera IMFMediaSource")
    assert camera.failures == [webcam.blocked_message()]


def test_a_camera_that_sends_no_picture_is_explained(qapp, backend):
    cam = webcam.Webcam(backend=backend, first_picture_timeout_ms=20)
    failures = []
    cam.failed.connect(failures.append)
    cam.start()
    QTest.qWait(150)
    assert failures == [webcam.NO_PICTURE] and backend.session.closed


def test_a_camera_that_sends_a_picture_in_time_is_fine(qapp, backend):
    cam = webcam.Webcam(backend=backend, first_picture_timeout_ms=60)
    failures = []
    cam.failed.connect(failures.append)
    cam.start()
    backend.session.on_frame(FakeVideoFrame(picture()))
    QTest.qWait(150)
    cam.stop()
    assert failures == []


# Words for people, and the camera's format


@pytest.mark.parametrize(("text", "present", "blocked", "started", "expected"), [
    ("anything", True, True, False, "BLOCKED"),
    ("", False, False, True, webcam.CAMERA_GONE),
    ("Device is BUSY", True, False, False, webcam.IN_USE),
    ("Failed to create camera IMFSourceReader", True, False, False,
     "The camera couldn't start (Failed to create camera IMFSourceReader). Another app may be using it: "
     "close that app, then try again."),
    ("", True, False, True, "The camera stopped working."),
])
def test_explain(text, present, blocked, started, expected):
    message = webcam.explain(text, present=present, blocked=blocked, started=started)
    assert message == (webcam.blocked_message() if expected == "BLOCKED" else expected)


def fmt(width, height, fps=30.0, jpeg=False):
    return width, height, fps, jpeg


def test_the_smallest_format_at_least_480_high_is_chosen():
    formats = [fmt(1280, 720), fmt(640, 360), fmt(640, 480), fmt(320, 240), fmt(1920, 1080)]
    assert webcam.choose_format(formats) == 2


def test_compressed_and_slow_formats_are_avoided_when_there_are_others():
    formats = [fmt(640, 480, jpeg=True), fmt(640, 480, fps=15.0), fmt(1280, 720)]
    assert webcam.choose_format(formats) == 2


def test_a_camera_with_only_small_formats_uses_its_largest():
    assert webcam.choose_format([fmt(320, 240), fmt(352, 288), fmt(176, 144)]) == 1


def test_a_camera_with_only_compressed_formats_still_gets_one():
    assert webcam.choose_format([fmt(1280, 720, jpeg=True), fmt(640, 480, jpeg=True)]) == 1


def test_a_camera_that_lists_no_formats_is_left_to_choose():
    assert webcam.choose_format([]) is None


# Windows' privacy setting


@pytest.mark.windows
@pytest.mark.parametrize(("values", "blocked"), [
    ([], False), (["Allow", "Allow"], False), (["Allow", "Deny"], True), (["deny"], True)])
def test_the_privacy_setting_blocks_when_any_switch_is_off(monkeypatch, values, blocked):
    monkeypatch.setattr(webcam, "_consent_values", lambda: values)
    assert webcam.camera_blocked() is blocked


@pytest.mark.windows
def test_the_privacy_setting_is_read_from_the_registry():
    assert all(isinstance(value, str) for value in webcam._consent_values())


# Startup cost


def test_importing_the_webcam_parts_doesnt_load_qt_multimedia():
    code = ("import sys; import snipforge.video.webcam, snipforge.video.webcam_bubble, "
            "snipforge.video.webcam_layer; sys.exit(1 if 'PySide6.QtMultimedia' in sys.modules else 0)")
    assert subprocess.run([sys.executable, "-c", code], timeout=60).returncode == 0
