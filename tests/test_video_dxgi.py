"""snipforge/video/dxgi.py: copying an area of one monitor with DXGI Desktop Duplication.

The COM side is replaced by a fake duplication here; tests/desktop/test_video_screen.py runs the
real thing. These cover the parts that are plain logic: which monitor an area is on, how a rotated
monitor's image is turned upright, and what grab() does with timeouts, unchanged frames, protected
content and Windows taking the duplication away (UAC prompt, lock screen).
"""
import ctypes

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




def test_a_turned_area_can_be_drawn_on(qapp):
    data, _stride = dxgi.upright(bytes(32), 8, 2, 4, 90)
    assert isinstance(data, bytearray)  # the recorder draws the pointer into it


@pytest.mark.windows
def test_structures_match_windows_x64_layouts():
    sizes = {dxgi._RECT: 16, dxgi._OUTPUT_DESC: 96, dxgi._OUTDUPL_DESC: 36, dxgi._FRAME_INFO: 48,
             dxgi._TEXTURE2D_DESC: 44, dxgi._BOX: 24, dxgi._MAPPED: 16, dxgi._ADAPTER_DESC: 304}
    assert {s.__name__: ctypes.sizeof(s) for s in sizes} == {s.__name__: n for s, n in sizes.items()}


def test_off_windows_the_screen_cant_be_duplicated(off_windows):
    off_windows(dxgi)
    assert not dxgi.available()
    assert dxgi.monitors() == []
    with pytest.raises(dxgi.DuplicationUnavailable, match="Windows"):
        dxgi.AreaDuplicator(0, 0, 100, 100)


# grab(), with a fake duplication


class FakeDuplication:
    """Stands in for the COM objects: each acquire() answers with the next scripted step, and each
    restart() with the next of `restarts` (None: it worked; an exception: raised)."""

    def __init__(self, script, restarts=(), rotation=0, size=(2, 1)):
        self.script = list(script)
        self.restarts = list(restarts)
        self.rotation, self.copy_size = rotation, size
        self.restart_calls = []  # new_device for each restart
        self.reads = 0
        self.closed = False

    def acquire(self, timeout_ms):
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        if step == "timeout":
            return None
        present, protected = step
        return dxgi.Acquired(present_time=present or None, protected=protected)

    def read(self):
        self.reads += 1
        width, height = self.copy_size
        return bytearray(range(width * height * 4)), width * 4

    def restart(self, *, new_device):
        self.restart_calls.append(new_device)
        result = self.restarts.pop(0)
        if result is not None:
            raise result

    def close(self):
        self.closed = True


def opener_of(fake):
    areas = []

    def open_area(area):
        areas.append(area)
        if isinstance(fake, BaseException):
            raise fake
        return fake
    open_area.areas = areas
    return open_area


class FakeTime:
    """dxgi's clock and sleep: sleeping moves the clock on."""

    def __init__(self):
        self.now = 100.0
        self.slept = []

    def perf_counter(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = FakeTime()
    monkeypatch.setattr(dxgi, "time", fake)
    return fake


LOST = dxgi.AccessLost("AcquireNextFrame: HRESULT 0x887A0026")
DENIED = com.ComError("DuplicateOutput1", dxgi.E_ACCESSDENIED)  # the lock screen is up


def test_the_area_is_opened_once_up_front():
    opener = opener_of(FakeDuplication([]))
    dup = dxgi.AreaDuplicator(10, 20, 2, 1, opener=opener)
    assert opener.areas == [(10, 20, 2, 1)]
    assert (dup.width, dup.height, dup.reopens, dup.lost) == (2, 1, 0, False)


def test_errors_opening_the_area_are_raised_straight_away():
    with pytest.raises(dxgi.AreaError):
        dxgi.AreaDuplicator(0, 0, 2, 1, opener=opener_of(dxgi.AreaError("isn't on any monitor")))
    with pytest.raises(dxgi.DuplicationUnavailable):
        dxgi.AreaDuplicator(0, 0, 2, 1, opener=opener_of(dxgi.DuplicationUnavailable("Remote Desktop")))


def test_a_timeout_gives_nothing():
    dup = dxgi.AreaDuplicator(0, 0, 2, 1, opener=opener_of(FakeDuplication(["timeout"])))
    assert dup.grab(16) is None


def test_frames_where_only_the_pointer_moved_give_nothing():
    # Windows' first frame after opening is one of these, with an empty image: never copied.
    fake = FakeDuplication([(0.0, False), (12.5, False), (0.0, False)])
    dup = dxgi.AreaDuplicator(0, 0, 2, 1, opener=opener_of(fake))

    assert dup.grab(16) is None
    assert dup.grab(16) == (bytes(range(8)), 8)
    assert dup.grab(16) is None  # only the pointer moved: the caller repeats its last frame
    assert fake.reads == 1
    assert dup.frame_time == 12.5  # when Windows showed that image, on time.perf_counter()'s clock


def test_protected_content_is_reported_per_frame():
    fake = FakeDuplication([(1.0, True), (2.0, False)])
    dup = dxgi.AreaDuplicator(0, 0, 2, 1, opener=opener_of(fake))
    dup.grab(16)
    assert dup.protected_content is True  # a DRM video on the monitor showed black
    dup.grab(16)
    assert dup.protected_content is False


def test_losing_the_duplication_restarts_it_on_the_same_graphics_device(clock):
    fake = FakeDuplication([LOST, (0.0, False), (3.0, False)], restarts=[None])
    dup = dxgi.AreaDuplicator(0, 0, 2, 1, opener=opener_of(fake))

    assert dup.grab(16) is None
    assert fake.restart_calls == [False]  # a new device takes ~0.25 s: keep it
    assert (dup.reopens, dup.lost) == (1, False)
    assert dup.grab(16) is None  # the new duplication's first frame: only the pointer
    assert dup.grab(16) is not None
    assert clock.slept == [] and not fake.closed


def test_a_lost_graphics_device_is_made_again():
    removed = dxgi.AccessLost("AcquireNextFrame: HRESULT 0x887A0005", device_lost=True)
    fake = FakeDuplication([removed], restarts=[None])
    dup = dxgi.AreaDuplicator(0, 0, 2, 1, opener=opener_of(fake))
    dup.grab(16)
    assert fake.restart_calls == [True]


def test_an_unexpected_failure_restarts_everything_rather_than_ending_the_recording():
    fake = FakeDuplication([com.ComError("Map", 0x8007000E), (1.0, False)], restarts=[None])
    dup = dxgi.AreaDuplicator(0, 0, 2, 1, opener=opener_of(fake))
    assert dup.grab(16) is None
    assert fake.restart_calls == [True] and dup.reopens == 1
    assert dup.grab(16) is not None


def test_while_windows_refuses_grab_waits_and_tries_again_now_and_then(clock):
    fake = FakeDuplication([LOST, (5.0, False)], restarts=[DENIED, DENIED, None])
    dup = dxgi.AreaDuplicator(0, 0, 2, 1, opener=opener_of(fake))

    assert dup.grab(16) is None  # lost, and Windows refuses the restart straight away
    assert dup.lost and dup.reopens == 0 and "0x80070005" in str(dup.last_error)
    for _ in range(10):
        assert dup.grab(16) is None  # waits out each timeout instead of spinning...
    assert len(fake.restart_calls) == 1  # ...and doesn't ask Windows every time
    assert clock.slept == [0.016] * 10

    clock.now += dxgi.RETRY_SECONDS
    assert dup.grab(16) is None  # asked again: still refused
    clock.now += dxgi.RETRY_SECONDS
    assert dup.grab(16) is not None  # unlocked
    assert len(fake.restart_calls) == 3
    assert (dup.lost, dup.reopens, dup.last_error) == (False, 1, None)


def test_an_area_that_disappears_keeps_trying_rather_than_ending_the_recording(clock):
    # A monitor unplugged or its resolution changed: the recorder keeps the sound going and can
    # show last_error; frames repeat the last image.
    gone = dxgi.AreaError("isn't on any monitor")
    fake = FakeDuplication([LOST], restarts=[gone, gone])
    dup = dxgi.AreaDuplicator(0, 0, 2, 1, opener=opener_of(fake))
    assert dup.grab(16) is None
    clock.now += dxgi.RETRY_SECONDS
    assert dup.grab(16) is None
    assert dup.lost and dup.last_error is gone


@pytest.mark.parametrize("degrees", [90, 180, 270])
def test_a_rotated_monitors_frames_come_out_upright(qapp, degrees):
    desktop = desktop_image(4, 3)
    texture = native_texture(desktop, degrees)

    class Rotated(FakeDuplication):
        def read(self):
            return raw(texture)

    fake = Rotated([(1.0, False)], rotation=degrees, size=(texture.width(), texture.height()))
    dup = dxgi.AreaDuplicator(0, 0, 4, 3, opener=opener_of(fake))
    assert pixels(*dup.grab(16), 4, 3) == pixels(*raw(desktop), 4, 3)


def test_closing_releases_the_duplication_and_is_final():
    fake = FakeDuplication([])
    dup = dxgi.AreaDuplicator(0, 0, 2, 1, opener=opener_of(fake))
    with dup:
        pass
    assert fake.closed
    dup.close()  # twice is fine
    assert not dup.lost
    with pytest.raises(ValueError, match="closed"):
        dup.grab(16)
