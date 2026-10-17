"""snipforge/video/dxgi.py: copying an area of one monitor with DXGI Desktop Duplication.

The COM side is replaced by a fake duplication here; tests/desktop/test_video_screen.py runs the
real thing. These cover the parts that are plain logic: which monitor an area is on, how a rotated
monitor's image is turned upright, and what grab() does with timeouts, unchanged frames, protected
content and Windows taking the duplication away (UAC prompt, lock screen).
"""
import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage

from snipforge.video import com, dxgi

PRIMARY = dxgi.Monitor(0, 0, 1920, 1080)
LEFT = dxgi.Monitor(-1280, 56, 0, 1080)  # a smaller monitor to the left of the primary one


# Which monitor an area is on


def test_an_area_inside_a_monitor_is_found():
    assert dxgi.monitor_for((100, 200, 640, 480), [LEFT, PRIMARY]) == 1
    assert dxgi.monitor_for((-1280, 56, 1280, 1024), [LEFT, PRIMARY]) == 0  # the whole left monitor
    assert dxgi.monitor_for((1919, 1079, 1, 1), [PRIMARY]) == 0  # the last pixel


@pytest.mark.parametrize("area, message", [
    ((5000, 5000, 100, 100), "isn't on any monitor"),
    ((-50, 100, 100, 100), "spans two monitors"),
    ((1900, 100, 100, 100), "goes past the edge"),
    ((100, 1000, 100, 100), "goes past the edge"),
    ((100, 100, 0, 100), "empty"),
    ((100, 100, 100, -5), "empty"),
])
def test_areas_off_one_monitor_are_refused_clearly(area, message):
    with pytest.raises(dxgi.AreaError, match=message):
        dxgi.monitor_for(area, [LEFT, PRIMARY])


def test_an_area_is_checked_against_the_monitor_its_top_left_is_on():
    # Starting on the left monitor's empty strip above it (y < 56) is off every monitor.
    with pytest.raises(dxgi.AreaError, match="isn't on any monitor"):
        dxgi.monitor_for((-100, 0, 50, 50), [LEFT, PRIMARY])


def test_rotation_comes_from_dxgis_mode_rotation():
    assert [dxgi.degrees(r) for r in (0, 1, 2, 3, 4)] == [0, 0, 90, 180, 270]
    with pytest.raises(ValueError):
        dxgi.degrees(9)


# Rotated monitors: the duplicated image is in the monitor's own (unrotated) orientation


def desktop_image(width: int, height: int) -> QImage:
    """A desktop where every pixel has its own colour."""
    image = QImage(width, height, QImage.Format_RGB32)
    for x in range(width):
        for y in range(height):
            image.setPixelColor(x, y, QColor(x * 20, y * 20, (x * 7 + y * 13) % 256))
    return image


def native_texture(desktop: QImage, degrees: int) -> QImage:
    """The image DXGI hands over for a monitor rotated by `degrees`, per Microsoft's Desktop
    Duplication sample (texture pixel tx, ty shows the desktop pixel computed below)."""
    w, h = desktop.width(), desktop.height()
    size = (w, h) if degrees in (0, 180) else (h, w)
    texture = QImage(*size, QImage.Format_RGB32)
    for tx in range(size[0]):
        for ty in range(size[1]):
            dx, dy = {0: (tx, ty), 90: (w - 1 - ty, tx), 180: (w - 1 - tx, h - 1 - ty),
                      270: (ty, h - 1 - tx)}[degrees]
            texture.setPixelColor(tx, ty, desktop.pixelColor(dx, dy))
    return texture


def raw(image: QImage) -> tuple[bytes, int]:
    return bytes(image.constBits())[:image.bytesPerLine() * image.height()], image.bytesPerLine()


def pixels(data: bytes, stride: int, width: int, height: int) -> list[list[str]]:
    image = QImage(data, width, height, stride, QImage.Format_RGB32)
    return [[image.pixelColor(x, y).name() for x in range(width)] for y in range(height)]


@pytest.mark.parametrize("degrees", [0, 90, 180, 270])
def test_a_rotated_monitors_area_comes_out_upright(qapp, degrees):
    desktop = desktop_image(9, 6)
    monitor = dxgi.Monitor(100, 50, 109, 56, rotation=degrees)
    area = (102, 51, 4, 3)  # x 2..5, y 1..3 on the monitor

    left, top, right, bottom = dxgi.texture_box(area, monitor)
    copied = native_texture(desktop, degrees).copy(QRect(left, top, right - left, bottom - top))
    data, stride = dxgi.upright(*raw(copied), copied.width(), copied.height(), degrees)

    assert pixels(data, stride, 4, 3) == pixels(*raw(desktop.copy(QRect(2, 1, 4, 3))), 4, 3)


def test_an_unrotated_area_is_passed_through_untouched():
    data = bytes(range(32))
    assert dxgi.upright(data, 16, 4, 2, 0) == (data, 16)


# grab(), with a fake duplication


class FakeDuplication:
    """Stands in for the COM objects: each acquire() answers with the next scripted result."""

    def __init__(self, script, rotation=0, size=(2, 1)):
        self.script = list(script)
        self.rotation, self.copy_size = rotation, size
        self.copies_forced = []
        self.reads = 0
        self.closed = False

    def acquire(self, timeout_ms, *, copy_always):
        self.copies_forced.append(copy_always)
        step = self.script.pop(0)
        if step == "lost":
            raise dxgi.AccessLost()
        if step == "timeout":
            return None
        present, protected = step
        return dxgi.Acquired(copied=bool(present) or copy_always, present_time=present or None, protected=protected)

    def read(self):
        self.reads += 1
        width, height = self.copy_size
        return bytes(range(width * height * 4)), width * 4

    def close(self):
        self.closed = True


class Opener:
    """Hands out fake duplications in turn; an exception in the list is raised instead."""

    def __init__(self, *results):
        self.results = list(results)
        self.areas = []

    def __call__(self, area):
        self.areas.append(area)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.fixture
def no_sleep(monkeypatch):
    waits = []
    monkeypatch.setattr(dxgi.time, "sleep", waits.append)
    return waits


def test_the_area_is_opened_once_up_front():
    first = FakeDuplication([])
    opener = Opener(first)
    dup = dxgi.AreaDuplicator(10, 20, 2, 1, opener=opener)
    assert opener.areas == [(10, 20, 2, 1)]
    assert (dup.width, dup.height, dup.reopens, dup.lost) == (2, 1, 0, False)


def test_errors_opening_the_area_are_raised_straight_away():
    with pytest.raises(dxgi.AreaError):
        dxgi.AreaDuplicator(0, 0, 2, 1, opener=Opener(dxgi.AreaError("isn't on any monitor")))
    with pytest.raises(dxgi.DuplicationUnavailable):
        dxgi.AreaDuplicator(0, 0, 2, 1, opener=Opener(dxgi.DuplicationUnavailable("Remote Desktop")))


def test_a_timeout_gives_nothing():
    dup = dxgi.AreaDuplicator(0, 0, 2, 1, opener=Opener(FakeDuplication(["timeout"])))
    assert dup.grab(16) is None


def test_the_first_frame_is_copied_even_if_only_the_pointer_moved():
    fake = FakeDuplication([(0.0, False), (0.0, False), (12.5, False)])
    dup = dxgi.AreaDuplicator(0, 0, 2, 1, opener=Opener(fake))

    assert dup.grab(16) == (bytes(range(8)), 8)  # nothing to repeat yet, so the screen as it is
    assert dup.grab(16) is None  # only the pointer moved: the caller repeats its last frame
    assert dup.grab(16) == (bytes(range(8)), 8)
    assert fake.copies_forced == [True, False, False]
    assert fake.reads == 2
    assert dup.frame_time == 12.5  # when Windows showed that image, on time.perf_counter()'s clock


def test_protected_content_is_reported_per_frame():
    fake = FakeDuplication([(1.0, True), (2.0, False)])
    dup = dxgi.AreaDuplicator(0, 0, 2, 1, opener=Opener(fake))
    dup.grab(16)
    assert dup.protected_content is True  # a DRM video in the area showed black
    dup.grab(16)
    assert dup.protected_content is False


def test_losing_the_duplication_reopens_it(no_sleep):
    first = FakeDuplication(["lost"])
    second = FakeDuplication([(0.0, False), (0.0, False)])
    dup = dxgi.AreaDuplicator(0, 0, 2, 1, opener=Opener(first, second))

    assert dup.grab(16) is None
    assert first.closed and (dup.reopens, dup.lost) == (1, False)
    assert dup.grab(16) is not None  # a fresh duplication: its first frame is always copied
    assert dup.grab(16) is None
    assert no_sleep == []


def test_while_windows_refuses_a_reopen_grab_waits_and_tries_again(no_sleep):
    denied = com.ComError("DuplicateOutput1", dxgi.E_ACCESSDENIED)  # the lock screen is up
    first = FakeDuplication(["lost"])
    second = FakeDuplication([(5.0, False)])
    dup = dxgi.AreaDuplicator(0, 0, 2, 1, opener=Opener(first, denied, denied, second))

    assert dup.grab(16) is None  # lost, and the first reopen is refused
    assert dup.lost and dup.reopens == 0 and "0x80070005" in str(dup.last_error)
    assert dup.grab(16) is None  # refused again: waits out the timeout instead of spinning
    assert no_sleep == [0.016]
    assert dup.grab(16) is not None  # unlocked
    assert (dup.lost, dup.reopens, dup.last_error) == (False, 1, None)


def test_an_area_that_disappears_keeps_trying_rather_than_ending_the_recording(no_sleep):
    # A monitor unplugged or its resolution changed: the recorder keeps the sound going and can
    # show last_error; frames repeat the last image.
    gone = dxgi.AreaError("isn't on any monitor")
    dup = dxgi.AreaDuplicator(0, 0, 2, 1, opener=Opener(FakeDuplication(["lost"]), gone, gone))
    assert dup.grab(16) is None
    assert dup.grab(16) is None
    assert dup.lost and dup.last_error is gone


def test_a_rotated_monitor_is_turned_upright(qapp):
    # The copied region is 1 wide and 2 tall in the monitor's own orientation: 2 by 1 upright.
    fake = FakeDuplication([(1.0, False)], rotation=90, size=(1, 2))
    dup = dxgi.AreaDuplicator(0, 0, 2, 1, opener=Opener(fake))
    data, stride = dup.grab(16)
    assert stride >= 8 and len(data) >= stride


def test_closing_releases_the_duplication_and_is_final():
    fake = FakeDuplication([])
    dup = dxgi.AreaDuplicator(0, 0, 2, 1, opener=Opener(fake))
    with dup:
        pass
    assert fake.closed
    dup.close()  # twice is fine
    with pytest.raises(ValueError, match="closed"):
        dup.grab(16)
