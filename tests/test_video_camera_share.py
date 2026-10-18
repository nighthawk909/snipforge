"""snipforge/video/camera_share.py: the webcam's picture from SnipForge to its recorder process, and
where its bubble is."""
import os
import pathlib
import struct
import subprocess
import sys
import threading
from multiprocessing import shared_memory

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
        struct.pack_into("<Q", writer._memory.buf, camera_share.SEQUENCE, 99)  # odd: mid-write, elsewhere

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


@pytest.mark.parametrize("name", ["", "Global\\\\thing", "sfcam-../x", "SnipForge-recorder-" + "0" * 32, 5, None])
def test_only_names_snipforge_makes_are_opened(name):
    assert not camera_share.is_name(name)
    with pytest.raises(ValueError):
        CameraReader(name)


def test_names_are_short_enough_for_macos():
    assert len("/" + CameraWriter.__init__.__globals__["NAME_PREFIX"] + "0" * 16) <= 31


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
                        (("a", 0, 50, 50), "circle"), ((10**30, 0, 50, 50), "circle"), ((0, 0, 50, 10**9), "circle")]:
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


def test_a_camera_that_stops_is_dropped_not_frozen_into_the_recording(qapp):
    clock = [100.0]
    writer = CameraWriter()
    reader = CameraReader(writer.name, now=lambda: clock[0])
    writer.write(picture(color="#445566"))
    assert reader.latest().pixelColor(0, 0).name() == "#445566"

    writer.close()  # unplugged, or SnipForge closed it
    clock[0] += 1.0
    assert reader.latest() is not None  # a moment's gap: keep the last picture
    clock[0] += camera_share.STALE_SECONDS
    assert reader.latest() is None  # gone for good: the recording leaves the camera out
    reader.close()


def test_a_picture_rewritten_while_it_is_copied_is_not_used(writer, qapp):
    reader = CameraReader(writer.name)
    try:
        writer.write(picture(color="#101010"))
        assert reader.latest().pixelColor(0, 0).name() == "#101010"
        writer.write(picture(color="#202020"))
        copy = reader._copy

        def copy_while_rewritten(size):
            data = copy(size)
            writer.write(picture(color="#303030"))  # SnipForge writes again mid-copy
            return data

        reader._copy = copy_while_rewritten
        assert reader.latest().pixelColor(0, 0).name() == "#101010"  # the torn copy was thrown away
        reader._copy = copy
        assert reader.latest().pixelColor(0, 0).name() == "#303030"
    finally:
        reader.close()


@pytest.mark.parametrize("header", [
    (b"NOTMAGIC", 2, 320, 240, 1280),
    (camera_share.MAGIC, 2, 99_999, 240, 1280),
    (camera_share.MAGIC, 2, 320, 240, 100),  # fewer bytes per line than the width needs
    (camera_share.MAGIC, 2, 0, 240, 1280),
])
def test_a_header_snipforge_didnt_write_is_ignored(writer, qapp, header):
    reader = CameraReader(writer.name)
    try:
        camera_share.HEADER.pack_into(writer._memory.buf, 0, *header)

        assert reader.latest() is None
    finally:
        reader.close()


def test_a_block_too_small_to_be_ours_is_refused(qapp):
    name = "sfcam-" + "e" * 16
    small = shared_memory.SharedMemory(name=name, create=True, size=4096)
    try:
        with pytest.raises(ValueError, match="too small"):
            CameraReader(name)
    finally:
        small.close()
        small.unlink()


def test_a_write_that_fails_halfway_leaves_the_next_one_readable(writer, qapp, monkeypatch):
    reader = CameraReader(writer.name)

    def out_of_memory(_data):
        raise MemoryError("out of memory")

    try:
        monkeypatch.setattr(camera_share, "bytes", out_of_memory, raising=False)  # the copy, mid-write
        with pytest.raises(MemoryError):
            writer.write(picture())
        monkeypatch.undo()
        writer.write(picture(color="#606060"))

        assert reader.latest().pixelColor(0, 0).name() == "#606060"
    finally:
        reader.close()


def test_the_picture_crosses_from_one_process_to_another(writer, qapp, tmp_path):
    writer.write(picture(color="#708090"))
    script = tmp_path / "read.py"
    script.write_text(
        "import sys\n"
        "from PySide6.QtGui import QGuiApplication\n"
        "app = QGuiApplication(['x', '-platform', 'offscreen'])\n"
        "from snipforge.video.camera_share import CameraReader\n"
        "reader = CameraReader(sys.argv[1])\n"
        "print(reader.latest().pixelColor(2, 2).name())\n"
        "reader.close()\n")

    root = str(pathlib.Path(camera_share.__file__).resolve().parents[2])  # this checkout, not an installed copy
    run = subprocess.run([sys.executable, str(script), writer.name], capture_output=True, text=True, timeout=60,
                         env={**os.environ, "PYTHONPATH": root})

    assert run.stdout.strip() == "#708090", run.stderr
    assert "leaked" not in run.stderr  # the recorder side never takes charge of SnipForge's block


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
              "camera_share": "sfcam-" + "a" * 16, "camera_bubble": (0, 0, 100, 100), **change}
    with pytest.raises(ValueError, match=reason):
        Settings(**values)


def test_camera_settings_go_through_the_channel(tmp_path):
    from pathlib import Path

    from snipforge.video.channel import settings_from_message, settings_to_message
    from snipforge.video.recorder import Settings

    settings = Settings((0, 0, 320, 240), Path(tmp_path).resolve() / "r.mp4",
                        camera_share="sfcam-" + "b" * 16, camera_bubble=(10, 20, 160, 160),
                        camera_shape="rounded")

    assert settings_from_message(settings_to_message(settings)) == settings
