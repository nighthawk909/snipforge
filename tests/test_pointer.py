"""The mouse pointer as an image, so a snip can include it."""
import sys

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage

from snipforge import pointer


def solid(color: str, w: int = 2, h: int = 1) -> QImage:
    image = QImage(w, h, QImage.Format_RGB32)
    image.fill(QColor(color))
    return image


def pair(on_black: list[str], on_white: list[str]) -> tuple[QImage, QImage]:
    """Two 1-row images, one pixel per entry: the pointer drawn on black and on white."""
    images = []
    for colors in (on_black, on_white):
        image = QImage(len(colors), 1, QImage.Format_RGB32)
        for x, color in enumerate(colors):
            image.setPixelColor(x, 0, QColor(color))
        images.append(image)
    return images[0], images[1]


def test_transparency_comes_from_the_two_backgrounds():
    on_black, on_white = pair(["#ff0000", "#000000", "#000080"], ["#ff0000", "#ffffff", "#7f7fff"])
    image, inverted = pointer.with_alpha(on_black, on_white)
    assert inverted == ()
    opaque, clear, half = (image.pixelColor(x, 0) for x in range(3))
    assert (opaque.red(), opaque.alpha()) == (255, 255)  # the same on both: solid
    assert clear.alpha() == 0  # black on black, white on white: the background showing through
    assert 126 <= half.alpha() <= 130 and half.blue() >= 250 and half.red() <= 2  # half-see-through blue


def test_inverting_pixels_are_listed_and_left_clear():
    # Old-style pointers (the text I-beam) invert what's under them: white on black, black on white.
    on_black, on_white = pair(["#ff0000", "#ffffff"], ["#ff0000", "#000000"])
    image, inverted = pointer.with_alpha(on_black, on_white)
    assert inverted == ((1, 0),) and image.pixelColor(1, 0).alpha() == 0


def test_inverting_pointers_invert_the_screen_under_them():
    screen = solid("#ffffff", 4, 1)
    screen.setPixelColor(2, 0, QColor("#1e3550"))
    clear = QImage(4, 1, QImage.Format_ARGB32)
    clear.fill(QColor(0, 0, 0, 0))
    shot = pointer.PointerShot(clear, QPoint(0, 0), inverted=((1, 0), (2, 0), (9, 9)))  # (9, 9) is off-screen
    result = pointer.composite(screen, shot, origin=QPoint(0, 0))
    assert [result.pixelColor(x, 0).name() for x in range(4)] == ["#ffffff", "#000000", "#e1caaf", "#ffffff"]


def test_with_alpha_needs_matching_images():
    with pytest.raises(ValueError):
        pointer.with_alpha(solid("#000000", 2, 2), solid("#ffffff", 3, 2))


def test_pointer_is_drawn_onto_a_copy_of_the_screen():
    screen = solid("#ffffff", 20, 20)
    arrow = QImage(4, 4, QImage.Format_ARGB32)
    arrow.fill(QColor(255, 0, 0, 255))
    shot = pointer.PointerShot(arrow, QPoint(105, 207))  # top-left, in screen pixels
    result = pointer.composite(screen, shot, origin=QPoint(100, 200))  # this monitor starts at 100,200
    assert result.pixelColor(5, 7).name() == "#ff0000" and result.pixelColor(8, 10).name() == "#ff0000"
    assert result.pixelColor(4, 7).name() == "#ffffff" and result.pixelColor(9, 7).name() == "#ffffff"
    assert screen.pixelColor(5, 7).name() == "#ffffff"  # the frozen screen itself is untouched


def test_pointer_lands_on_physical_pixels_on_a_scaled_monitor():
    screen = solid("#ffffff", 40, 40)
    screen.setDevicePixelRatio(2.0)  # a 200% monitor: the frozen screen is in physical pixels
    arrow = QImage(4, 4, QImage.Format_ARGB32)
    arrow.fill(QColor("#ff0000"))
    result = pointer.composite(screen, pointer.PointerShot(arrow, QPoint(10, 10)), origin=QPoint(0, 0))
    assert result.pixelColor(11, 11).name() == "#ff0000" and result.pixelColor(21, 21).name() == "#ffffff"
    assert result.devicePixelRatio() == 2.0


def test_pointer_on_another_monitor_leaves_this_one_alone():
    screen = solid("#ffffff", 20, 20)
    arrow = QImage(4, 4, QImage.Format_ARGB32)
    arrow.fill(QColor("#ff0000"))
    result = pointer.composite(screen, pointer.PointerShot(arrow, QPoint(-500, 5)), origin=QPoint(0, 0))
    assert result == screen


def test_grab_is_nothing_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert pointer.grab() is None


def test_grab_failures_are_quiet(monkeypatch):
    def broken():
        raise OSError("no cursor here")
    monkeypatch.setattr(pointer, "_grab_windows", broken)
    monkeypatch.setattr(sys, "platform", "win32")
    assert pointer.grab() is None


@pytest.mark.desktop
@pytest.mark.skipif(sys.platform != "win32", reason="Windows pointer")
def test_grab_returns_the_real_pointer():
    shot = pointer.grab()
    if shot is None:
        pytest.skip("no pointer showing in this session")
    assert 8 <= shot.image.width() <= 512 and 8 <= shot.image.height() <= 512
    assert shot.image.hasAlphaChannel()
    alphas = {shot.image.pixelColor(x, y).alpha() for x in range(shot.image.width())
              for y in range(shot.image.height())}
    # A shape, not a box: some pixels clear, and some solid or (text pointers) inverting.
    assert 0 in alphas and (255 in alphas or shot.inverted)
