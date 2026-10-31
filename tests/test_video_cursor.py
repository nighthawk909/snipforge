"""snipforge/video/cursor.py: the pointer, its highlight and click rings, drawn into recorded frames.

A fake pointer source stands in for Windows here (GetCursorInfo, GetAsyncKeyState); the desktop
suite (tests/desktop/test_video_screen.py) uses the real one.
"""
import pytest
from PySide6.QtGui import QColor, QImage

from snipforge.video import cursor

DOWN = -32768  # GetAsyncKeyState's high bit, as a signed SHORT: the button is down now
PRESSED = 1  # its low bit: pressed since the last poll
AREA = (100, 50, 80, 60)  # the recorded area, in physical desktop pixels


def square(colour: str = "#0000ff", size: int = 3, hotspot: tuple[int, int] = (1, 1)) -> cursor.PointerShape:
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor(colour))
    return cursor.PointerShape(image, hotspot)


class FakeSource:
    """The pointer and mouse buttons as a test sets them; records what was asked."""

    def __init__(self):
        self.position = (130, 80)
        self.visible = True
        self.handle = 7
        self.keys = (0, 0)  # GetAsyncKeyState for the physical left and right buttons
        self.swap = False
        self.shapes = {7: square(), 8: square("#00ff00")}
        self.calls = []
        self.renders = []
        self.available = True

    def state(self):
        self.calls.append("state")
        if not self.available:
            return None
        return cursor.PointerState(*self.position, self.visible, self.handle)

    def buttons(self):
        self.calls.append("buttons")
        keys = self.keys
        self.keys = tuple(k & ~PRESSED for k in keys)  # reading clears "pressed since", as Windows does
        return keys

    def swapped(self):
        self.calls.append("swapped")
        return self.swap

    def shape(self, handle):
        self.renders.append(handle)
        return self.shapes.get(handle)

    def standard_handles(self):
        return (7, 8)


def frame(colour: str = "#ffffff", size: tuple[int, int] = AREA[2:]) -> QImage:
    image = QImage(*size, QImage.Format_RGB32)
    image.fill(QColor(colour))
    return image


def at(image: QImage, x: int, y: int) -> QColor:
    return image.pixelColor(x, y)


def is_white(colour: QColor) -> bool:
    return colour.name() == "#ffffff"


def overlay(source, **effects) -> cursor.PointerOverlay:
    return cursor.PointerOverlay(AREA, cursor.PointerEffects(**effects), source=source)


# The pointer and its highlight


def test_the_pointer_is_drawn_at_its_hotspot_relative_to_the_area(qapp):
    source = FakeSource()  # at (130, 80) on the desktop: (30, 30) in the area
    image = frame()
    overlay(source, highlight=False, clicks=False).draw(image, 1.0)
    blue = [(x, y) for x in range(80) for y in range(60) if at(image, x, y).name() == "#0000ff"]
    assert sorted(blue) == [(x, y) for x in (29, 30, 31) for y in (29, 30, 31)]


def test_the_highlight_sits_under_the_pointer(qapp):
    image = frame()
    overlay(FakeSource(), pointer=False, clicks=False).draw(image, 1.0)
    centre = at(image, 30, 30)
    assert centre.red() == 255 and centre.blue() < 230  # tinted yellow
    assert not is_white(at(image, 30 + cursor.HIGHLIGHT_RADIUS - 3, 30))
    assert is_white(at(image, 30 + cursor.HIGHLIGHT_RADIUS + 2, 30))


def test_the_pointer_is_drawn_over_its_highlight(qapp):
    image = frame()
    overlay(FakeSource(), clicks=False).draw(image, 1.0)
    assert at(image, 30, 30).name() == "#0000ff"
    assert at(image, 35, 30).blue() < 230


def test_a_hidden_pointer_has_no_pointer_and_no_highlight(qapp):
    source = FakeSource()
    source.visible = False  # a game hid it, or the last input was touch
    image = frame()
    overlay(source).draw(image, 1.0)
    assert image == frame()


def test_a_pointer_outside_the_area_draws_nothing(qapp):
    source = FakeSource()
    source.position = (20, 10)
    image = frame()
    overlay(source).draw(image, 1.0)
    assert image == frame()


def test_a_smaller_frame_gets_everything_scaled_to_fit(qapp):
    # The recorder may shrink a large area (1080p cap): the frame is half the area's size here.
    image = frame(size=(40, 30))
    overlay(FakeSource(), highlight=False, clicks=False).draw(image, 1.0)
    assert at(image, 15, 15).name() == "#0000ff"  # (30, 30) in the area
    assert is_white(at(image, 30, 29))  # where it would be unscaled


def test_a_scaled_down_pointer_is_smoothed(qapp):
    stripes = QImage(4, 4, QImage.Format_ARGB32_Premultiplied)
    for x in range(4):
        for y in range(4):
            stripes.setPixelColor(x, y, QColor("black" if x % 2 else "white"))
    source = FakeSource()
    source.shapes[7] = cursor.PointerShape(stripes, (0, 0))
    image = frame("#ff0000", size=(40, 30))
    overlay(source, highlight=False, clicks=False).draw(image, 1.0)
    assert 60 < at(image, 15, 15).green() < 200  # grey, not one stripe or the other


def test_pointers_that_invert_the_screen_do_so(qapp):
    clear = QImage(1, 1, QImage.Format_ARGB32_Premultiplied)
    clear.fill(QColor(0, 0, 0, 0))
    mask = QImage(1, 1, QImage.Format_ARGB32_Premultiplied)
    mask.fill(QColor("white"))
    source = FakeSource()
    source.shapes[7] = cursor.PointerShape(clear, (0, 0), inverted=mask)  # the text I-beam does this
    image = frame("#1e3550")
    overlay(source, highlight=False, clicks=False).draw(image, 1.0)
    assert at(image, 30, 30).name() == "#e1caaf"
    assert at(image, 31, 30).name() == "#1e3550"


# Click rings


def poll(ov: cursor.PointerOverlay, now: float) -> QImage:
    image = frame()
    ov.draw(image, now)
    return image


def ring_strength(colour: QColor) -> int:
    """How far a pixel is from white: 0 for no ring."""
    return 765 - colour.red() - colour.green() - colour.blue()


def test_a_click_makes_a_ring_that_grows_and_fades(qapp):
    source = FakeSource()
    ov = overlay(source, pointer=False, highlight=False)
    poll(ov, 0.0)
    source.keys = (DOWN | PRESSED, 0)
    poll(ov, 1.0)
    source.keys = (0, 0)
    assert ov.clicks_seen == 1

    small, big = cursor.RING_RADII
    halfway = poll(ov, 1.0 + cursor.RING_SECONDS / 2)
    radius = round(small + (big - small) / 2)
    assert ring_strength(at(halfway, 30 + radius, 30)) > 100
    assert is_white(at(halfway, 30 + radius + 6, 30)) and is_white(at(halfway, 30, 30))

    late = poll(ov, 1.0 + cursor.RING_SECONDS * 0.9)
    radius = round(small + (big - small) * 0.9)
    assert 0 < ring_strength(at(late, 30 + radius, 30)) < ring_strength(at(halfway, 30 + round(small + 14), 30))

    assert poll(ov, 1.0 + cursor.RING_SECONDS + 0.01) == frame()  # gone


def test_holding_a_button_makes_one_ring(qapp):
    source = FakeSource()
    ov = overlay(source)
    poll(ov, 0.0)
    source.keys = (DOWN | PRESSED, 0)
    for t in (0.1, 0.13, 0.16, 0.2):
        poll(ov, t)
        source.keys = (DOWN, 0)
    assert ov.clicks_seen == 1


def test_a_click_between_two_frames_is_still_caught(qapp):
    source = FakeSource()
    ov = overlay(source)
    poll(ov, 0.0)
    source.keys = (PRESSED, 0)  # down and up again since the last frame
    poll(ov, 0.1)
    assert ov.clicks_seen == 1


def test_a_button_already_down_when_recording_starts_is_not_a_click(qapp):
    source = FakeSource()
    source.keys = (DOWN | PRESSED, PRESSED)  # held, and an old click Windows still remembers
    ov = overlay(source)
    poll(ov, 0.0)
    source.keys = (0, 0)
    poll(ov, 0.1)
    assert ov.clicks_seen == 0
    source.keys = (DOWN | PRESSED, 0)
    poll(ov, 0.2)
    assert ov.clicks_seen == 1


def ring_colour(source: FakeSource, keys) -> QColor:
    ov = overlay(source, pointer=False, highlight=False)
    poll(ov, 0.0)
    source.keys = keys
    poll(ov, 1.0)
    radius = round(cursor.RING_RADII[0] + (cursor.RING_RADII[1] - cursor.RING_RADII[0]) * 0.2)
    return at(poll(ov, 1.0 + cursor.RING_SECONDS * 0.2), 30 + radius, 30)


def test_left_and_right_clicks_have_different_colours(qapp):
    left = ring_colour(FakeSource(), (DOWN | PRESSED, 0))
    right = ring_colour(FakeSource(), (0, DOWN | PRESSED))
    assert left.red() > left.blue()  # orange
    assert right.blue() > right.red()  # blue


def test_swapped_mouse_buttons_keep_the_colours_right(qapp):
    # GetAsyncKeyState reads the physical buttons: with the buttons swapped for a left-handed
    # mouse, the physical left button is the logical right one.
    source = FakeSource()
    source.swap = True
    colour = ring_colour(source, (DOWN | PRESSED, 0))
    assert colour.blue() > colour.red()


def test_rings_stay_where_the_click_happened(qapp):
    source = FakeSource()
    ov = overlay(source, pointer=False, highlight=False)
    poll(ov, 0.0)
    source.keys = (DOWN | PRESSED, 0)
    poll(ov, 1.0)
    source.keys, source.position = (0, 0), (160, 100)  # the pointer moves on
    radius = round(cursor.RING_RADII[0] + (cursor.RING_RADII[1] - cursor.RING_RADII[0]) * 0.2)
    image = poll(ov, 1.0 + cursor.RING_SECONDS * 0.2)
    assert ring_strength(at(image, 30 + radius, 30)) > 100


def test_rings_are_kept_while_the_pointer_cant_be_read(qapp):
    source = FakeSource()
    ov = overlay(source, pointer=False, highlight=False)
    poll(ov, 0.0)
    source.keys = (DOWN | PRESSED, 0)
    poll(ov, 1.0)
    source.keys, source.available = (0, 0), False  # GetCursorInfo failed (the secure desktop)
    radius = round(cursor.RING_RADII[0] + (cursor.RING_RADII[1] - cursor.RING_RADII[0]) * 0.2)
    image = poll(ov, 1.0 + cursor.RING_SECONDS * 0.2)
    assert ring_strength(at(image, 30 + radius, 30)) > 100


# Switching things off


def test_with_click_rings_off_the_buttons_are_never_read(qapp):
    source = FakeSource()
    source.keys = (DOWN | PRESSED, 0)
    ov = overlay(source, clicks=False)
    poll(ov, 0.0)
    poll(ov, 0.1)
    assert "buttons" not in source.calls and "swapped" not in source.calls
    assert ov.clicks_seen == 0


def test_with_everything_off_nothing_is_read_or_drawn(qapp):
    source = FakeSource()
    image = frame()
    overlay(source, pointer=False, highlight=False, clicks=False).draw(image, 1.0)
    assert source.calls == [] and image == frame()


def test_off_windows_there_is_no_pointer_to_draw(qapp, off_windows):
    off_windows(cursor)
    ov = cursor.PointerOverlay(AREA)
    image = frame()
    ov.draw(image, 1.0)
    ov.prewarm()
    assert image == frame()


# The pointer shape cache


def test_each_pointer_shape_is_rendered_once(qapp):
    source = FakeSource()
    ov = overlay(source, highlight=False, clicks=False)
    for t in range(5):
        poll(ov, t / 30)
    source.handle = 8
    poll(ov, 0.2)
    source.handle = 7
    poll(ov, 0.25)
    assert source.renders == [7, 8]


def test_the_cache_forgets_the_least_recently_used_shape():
    renders = []
    cache = cursor.ShapeCache(lambda handle: renders.append(handle) or handle, limit=2)
    for handle in (1, 2, 1, 3, 1, 2):
        cache.get(handle, now=0.0)
    assert renders == [1, 2, 3, 2]  # 2 was the least recently used when 3 came; 1 stayed
    assert len(cache) == 2


def test_a_pointer_that_couldnt_be_drawn_is_retried_now_and_then():
    renders = []
    cache = cursor.ShapeCache(lambda handle: renders.append(handle), retry_after=1.0)
    assert cache.get(5, now=0.0) is None
    assert cache.get(5, now=0.5) is None  # not every frame: rendering is slow
    cache.get(5, now=1.1)
    assert renders == [5, 5]


def test_prewarm_draws_the_standard_pointers_before_recording(qapp):
    source = FakeSource()
    ov = overlay(source, clicks=False)
    ov.prewarm()
    assert sorted(source.renders) == [7, 8]
    poll(ov, 0.0)
    assert sorted(source.renders) == [7, 8]


def test_a_shape_is_made_from_the_pointer_drawn_on_black_and_on_white(qapp):
    # The same conversion snips use (pointer.with_alpha), kept ready to draw.
    on_black, on_white = QImage(3, 1, QImage.Format_RGB32), QImage(3, 1, QImage.Format_RGB32)
    for x, (b, w) in enumerate([("#ff0000", "#ff0000"), ("#000000", "#ffffff"), ("#ffffff", "#000000")]):
        on_black.setPixelColor(x, 0, QColor(b))
        on_white.setPixelColor(x, 0, QColor(w))
    shape = cursor.shape_from_drawings(on_black, on_white, (2, 0))
    assert shape.hotspot == (2, 0)
    assert shape.image.format() == QImage.Format_ARGB32_Premultiplied
    assert [shape.image.pixelColor(x, 0).alpha() for x in range(3)] == [255, 0, 0]
    assert [shape.inverted.pixelColor(x, 0).alpha() for x in range(3)] == [0, 0, 255]


def test_a_shape_with_nothing_inverted_has_no_mask(qapp):
    solid = QImage(2, 2, QImage.Format_RGB32)
    solid.fill(QColor("#123456"))
    assert cursor.shape_from_drawings(solid, solid, (0, 0)).inverted is None


@pytest.mark.parametrize("effects, expected", [
    ({}, True), ({"pointer": False, "highlight": False, "clicks": False}, False), ({"pointer": False}, True),
])
def test_effects_say_whether_anything_is_on(effects, expected):
    assert cursor.PointerEffects(**effects).any is expected
