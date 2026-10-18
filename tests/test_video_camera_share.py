"""snipforge/video/camera_share.py: the webcam's picture from SnipForge to its recorder process, and
where its bubble is."""
import threading

import pytest
from PySide6.QtGui import QColor, QImage

from snipforge.video import camera_share
from snipforge.video.camera_share import BubbleState, CameraReader, CameraWriter


def picture(width=320, height=240, color="#3080c0") -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(color))
    return image


@pytest.fixture
def writer():
    shared = CameraWriter()
    yield shared
    shared.close()


def test_a_picture_written_is_read_on_the_other_side(writer, qapp):
    reader = CameraReader(writer.name)
    try:
        assert reader.latest() is None  # nothing yet

        writer.write(picture())

        got = reader.latest()
        assert (got.width(), got.height()) == (320, 240)
        assert got.pixelColor(10, 10).name() == "#3080c0"
    finally:
        reader.close()


def test_only_the_newest_picture_counts(writer, qapp):
    reader = CameraReader(writer.name)
    try:
        writer.write(picture(color="#ff0000"))
        writer.write(picture(color="#00ff00"))

        assert reader.latest().pixelColor(5, 5).name() == "#00ff00"
        assert reader.latest().pixelColor(5, 5).name() == "#00ff00"  # read again: the same picture
    finally:
        reader.close()


def test_a_big_picture_is_shrunk_to_fit(writer, qapp):
    reader = CameraReader(writer.name)
    try:
        writer.write(picture(1920, 1080))

        got = reader.latest()
        assert got.width() <= camera_share.MAX_WIDTH and got.height() <= camera_share.MAX_HEIGHT
        assert got.width() / got.height() == pytest.approx(1920 / 1080, abs=0.01)
    finally:
        reader.close()


def test_a_picture_half_written_is_never_read(writer, qapp):
    reader = CameraReader(writer.name)
    try:
        writer.write(picture(color="#112233"))
        assert reader.latest().pixelColor(0, 0).name() == "#112233"
        writer._begin_write()  # as if the other process were in the middle of writing

        assert reader.latest().pixelColor(0, 0).name() == "#112233"  # the last whole one
    finally:
        reader.close()


def test_pictures_written_while_being_read_are_never_torn(writer, qapp):
    reader = CameraReader(writer.name)
    colors = ["#ff0000", "#00ff00", "#0000ff"]
    stop, torn = threading.Event(), []

    def keep_writing():
        n = 0
        while not stop.is_set():
            writer.write(picture(color=colors[n % 3]))
            n += 1

    thread = threading.Thread(target=keep_writing)
    thread.start()
    try:
        for _ in range(200):
            got = reader.latest()
            if got is not None and got.pixelColor(0, 0) != got.pixelColor(got.width() - 1, got.height() - 1):
                torn.append(got)
    finally:
        stop.set()
        thread.join()
        reader.close()
    assert torn == []


@pytest.mark.parametrize("name", ["", "Global\\\\thing", "SnipForge-camera-../x", "SnipForge-recorder-" + "0" * 32])
def test_only_names_snipforge_makes_are_opened(name):
    with pytest.raises(ValueError):
        CameraReader(name)


def test_the_bubbles_place_is_kept_for_the_frame_thread():
    bubble = BubbleState((1500, 800, 240, 240), "circle")

    assert bubble.snapshot().rect == (1500, 800, 240, 240) and bubble.snapshot().shape == "circle"
    bubble.update((100, 100, 320, 180), "rounded")
    assert (bubble.snapshot().rect, bubble.snapshot().shape) == ((100, 100, 320, 180), "rounded")
    bubble.update(None, "rounded")  # hidden
    assert bubble.snapshot() is None


def test_a_bubble_place_that_makes_no_sense_is_ignored():
    bubble = BubbleState((1500, 800, 240, 240), "circle")

    for rect, shape in [((1, 2, 3), "circle"), ((0, 0, -5, 10), "circle"), ((0, 0, 50, 50), "star"),
                        (("a", 0, 50, 50), "circle")]:
        bubble.update(rect, shape)

    assert bubble.snapshot().rect == (1500, 800, 240, 240)


def test_the_writer_sends_pictures_as_rgb32(writer, qapp):
    reader = CameraReader(writer.name)
    try:
        writer.write(picture().convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied))  # as Qt's camera gives
        assert reader.latest().format() == QImage.Format.Format_RGB32
        assert reader.latest().pixelColor(1, 1).name() == "#3080c0"
    finally:
        reader.close()


def test_a_closed_writer_leaves_a_reader_with_its_last_picture(qapp):
    writer = CameraWriter()
    reader = CameraReader(writer.name)
    writer.write(picture(color="#445566"))
    reader.latest()

    writer.close()

    assert reader.latest().pixelColor(0, 0).name() == "#445566"
    reader.close()


# --- in the recording -----------------------------------------------------------------------------

def test_with_the_camera_on_the_recordings_overlay_draws_it_under_the_pointer(writer, qapp, tmp_path):
    from pathlib import Path

    from snipforge.video.camera_share import BubbleState
    from snipforge.video.recorder import Settings, _real_overlay
    from snipforge.video.webcam_layer import CompositeOverlay, WebcamLayer

    settings = Settings((0, 0, 640, 360), Path(tmp_path) / "r.mp4", camera_share=writer.name,
                        camera_bubble=(400, 200, 160, 120), camera_shape="rectangle")
    bubble = BubbleState(settings.camera_bubble, settings.camera_shape)
    overlay = _real_overlay(settings, bubble)
    writer.write(picture(color="#00ff00"))

    assert isinstance(overlay, CompositeOverlay) and isinstance(overlay.overlays[0], WebcamLayer)
    frame = picture(640, 360, "#000000").convertToFormat(QImage.Format.Format_RGB32)
    overlay.draw(frame, 0.0)
    assert frame.pixelColor(480, 260).name() == "#00ff00"  # the camera, in the bubble
    assert frame.pixelColor(100, 100).name() == "#000000"  # nothing elsewhere
    bubble.update((0, 0, 100, 100), "rectangle")  # SnipForge moved it
    overlay.draw(frame, 0.0)
    assert frame.pixelColor(50, 50).name() == "#00ff00"


def test_without_the_camera_the_overlay_is_just_the_pointer(qapp, tmp_path):
    from pathlib import Path

    from snipforge.video.cursor import PointerOverlay
    from snipforge.video.recorder import Settings, _real_overlay

    assert isinstance(_real_overlay(Settings((0, 0, 640, 360), Path(tmp_path) / "r.mp4")), PointerOverlay)


@pytest.mark.parametrize("change, reason", [
    ({"camera_share": "not-a-share"}, "camera"),
    ({"camera_bubble": (1, 2, 3)}, "camera"),
    ({"camera_shape": "star"}, "camera"),
])
def test_camera_settings_that_make_no_sense_are_refused(change, reason, tmp_path):
    from pathlib import Path

    from snipforge.video.recorder import Settings

    values = {"area": (0, 0, 320, 240), "output": Path(tmp_path) / "r.mp4",
              "camera_share": "SnipForge-camera-" + "a" * 32, "camera_bubble": (0, 0, 100, 100), **change}
    with pytest.raises(ValueError, match=reason):
        Settings(**values)


def test_camera_settings_go_through_the_channel(tmp_path):
    from pathlib import Path

    from snipforge.video.channel import settings_from_message, settings_to_message
    from snipforge.video.recorder import Settings

    settings = Settings((0, 0, 320, 240), Path(tmp_path).resolve() / "r.mp4",
                        camera_share="SnipForge-camera-" + "b" * 32, camera_bubble=(10, 20, 160, 160),
                        camera_shape="rounded")

    assert settings_from_message(settings_to_message(settings)) == settings
