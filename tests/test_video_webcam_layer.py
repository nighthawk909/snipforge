"""snipforge/video/webcam_layer.py: the camera drawn into recorded frames where the bubble sits,
and CompositeOverlay, which puts the camera under the pointer."""
import threading

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter

from snipforge.video import webcam_layer
from snipforge.video.webcam_bubble import BubbleSnapshot

AREA = (100, 50, 200, 150)  # the recorded area, in physical desktop pixels
WHITE, RED, BLUE = "#ffffff", "#ff0000", "#0000ff"


def camera_picture(size=(80, 60)) -> QImage:
    """Red on the left, blue on the right, as the camera sees it."""
    image = QImage(*size, QImage.Format_RGB32)
    image.fill(QColor(BLUE))
    painter = QPainter(image)
    painter.fillRect(0, 0, size[0] // 2, size[1], QColor(RED))
    painter.end()
    return image


class FakeCamera:
    def __init__(self, image=None):
        self.image = image

    def latest(self):
        return self.image


class FakeBubble:
    def __init__(self, rect=(140, 80, 60, 60), shape=webcam_layer.CIRCLE):
        self.snap = BubbleSnapshot(rect, shape) if rect else None

    def snapshot(self):
        return self.snap


def frame(size=AREA[2:]) -> QImage:
    image = QImage(*size, QImage.Format_RGB32)
    image.fill(QColor(WHITE))
    return image


def colour(image, x, y) -> str:
    return image.pixelColor(x, y).name()


def painted(image) -> list[tuple[int, int]]:
    return [(x, y) for y in range(image.height()) for x in range(image.width()) if colour(image, x, y) != WHITE]


def draw(rect=(140, 80, 60, 60), shape=webcam_layer.CIRCLE, image=None, size=AREA[2:]) -> QImage:
    target = frame(size)
    layer = webcam_layer.WebcamLayer(AREA, FakeCamera(camera_picture() if image is None else image),
                                     FakeBubble(rect, shape))
    layer.draw(target, 1.0)
    return target


# Where and how it's drawn


def test_the_camera_is_drawn_where_the_bubble_is_relative_to_the_area(qapp):
    image = draw(shape=webcam_layer.RECTANGLE)  # the bubble at (140, 80) is (40, 30) in the area
    xs, ys = zip(*painted(image), strict=True)
    assert (min(xs), min(ys), max(xs), max(ys)) == (40, 30, 99, 89)


def test_the_camera_is_mirrored_like_a_mirror(qapp):
    image = draw(shape=webcam_layer.RECTANGLE)
    assert colour(image, 50, 60) == BLUE  # the camera's right side shows on the left
    assert colour(image, 90, 60) == RED


def test_the_mirror_can_be_left_out(qapp):
    target = frame()
    webcam_layer.WebcamLayer(AREA, FakeCamera(camera_picture()), FakeBubble(shape=webcam_layer.RECTANGLE),
                             mirror=False).draw(target, 1.0)
    assert colour(target, 50, 60) == RED and colour(target, 90, 60) == BLUE


def test_a_circle_leaves_the_corners_alone(qapp):
    image = draw(shape=webcam_layer.CIRCLE)
    assert colour(image, 41, 31) == WHITE and colour(image, 98, 88) == WHITE
    assert colour(image, 70, 31) != WHITE and colour(image, 41, 60) != WHITE  # its edges touch the sides
    assert colour(image, 55, 60) == BLUE and colour(image, 85, 60) == RED


def test_a_rounded_rectangle_rounds_only_its_corners(qapp):
    image = draw(shape=webcam_layer.ROUNDED)
    assert colour(image, 40, 30) == WHITE and colour(image, 99, 89) == WHITE
    assert colour(image, 40, 45) != WHITE and colour(image, 52, 30) != WHITE


def test_a_rectangle_fills_its_corners(qapp):
    image = draw(shape=webcam_layer.RECTANGLE)
    assert colour(image, 40, 30) == BLUE and colour(image, 99, 89) == RED


def test_a_camera_picture_of_another_shape_is_cropped_to_fill_not_squashed(qapp):
    tall = camera_picture(size=(40, 120))  # far taller than the bubble: its top and bottom are cut off
    image = draw(shape=webcam_layer.RECTANGLE, image=tall)
    assert colour(image, 45, 60) == BLUE and colour(image, 95, 60) == RED
    assert len(painted(image)) == 60 * 60


def test_a_bubble_partly_outside_the_area_is_drawn_where_it_overlaps(qapp):
    image = draw(rect=(70, 30, 60, 60), shape=webcam_layer.RECTANGLE)  # (-30, -20) in the area
    xs, ys = zip(*painted(image), strict=True)
    assert (min(xs), min(ys), max(xs), max(ys)) == (0, 0, 29, 39)


def test_a_smaller_frame_is_drawn_to_scale(qapp):
    image = draw(shape=webcam_layer.RECTANGLE, size=(100, 75))  # the recorder halved a big area
    xs, ys = zip(*painted(image), strict=True)
    assert (min(xs), min(ys), max(xs), max(ys)) == (20, 15, 49, 44)


def test_it_draws_from_the_frame_thread(qapp):
    target = frame()
    layer = webcam_layer.WebcamLayer(AREA, FakeCamera(camera_picture()), FakeBubble(shape=webcam_layer.RECTANGLE))
    worker = threading.Thread(target=layer.draw, args=(target, 1.0))
    worker.start()
    worker.join(5)
    assert colour(target, 50, 60) == BLUE and colour(target, 90, 60) == RED


# When nothing is drawn


@pytest.mark.parametrize("rect", [(400, 400, 60, 60), (40, 50, 60, 60), (100, 200, 60, 60), (300, 80, 50, 50)])
def test_a_bubble_outside_the_area_draws_nothing(qapp, rect):
    assert draw(rect=rect) == frame()


def test_a_hidden_bubble_draws_nothing(qapp):
    assert draw(rect=None) == frame()


def test_no_picture_yet_draws_nothing(qapp):
    target = frame()
    webcam_layer.WebcamLayer(AREA, FakeCamera(None), FakeBubble()).draw(target, 1.0)
    webcam_layer.WebcamLayer(AREA, FakeCamera(QImage()), FakeBubble()).draw(target, 1.0)
    assert target == frame()


def test_paint_camera_with_an_empty_target_draws_nothing(qapp):
    target = frame()
    painter = QPainter(target)
    webcam_layer.paint_camera(painter, camera_picture(), QRectF(10, 10, 0, 0), webcam_layer.CIRCLE)
    painter.end()
    assert target == frame()


def test_prewarm_has_nothing_to_make(qapp):
    webcam_layer.WebcamLayer(AREA, FakeCamera(), FakeBubble()).prewarm()


# CompositeOverlay


class Filler:
    """An overlay that fills a rectangle, recording each call."""

    def __init__(self, name, colour, calls, rect=(0, 0, 50, 50)):
        self.name, self.colour, self.calls, self.rect = name, colour, calls, rect
        self.prewarmed = []

    def draw(self, image, now):
        self.calls.append((self.name, now))
        painter = QPainter(image)
        painter.fillRect(*self.rect, QColor(self.colour))
        painter.end()

    def prewarm(self, now=None):
        self.prewarmed.append(now)


class Broken:
    def __init__(self):
        self.calls = 0

    def draw(self, image, now):
        self.calls += 1
        raise RuntimeError("camera glitch")


def test_overlays_are_drawn_in_order_the_first_at_the_bottom(qapp):
    calls = []
    camera = Filler("camera", RED, calls, rect=(0, 0, 50, 50))
    pointer = Filler("pointer", BLUE, calls, rect=(25, 25, 50, 50))
    target = frame()

    webcam_layer.CompositeOverlay(camera, pointer).draw(target, 2.5)

    assert calls == [("camera", 2.5), ("pointer", 2.5)]
    assert colour(target, 30, 30) == BLUE and colour(target, 10, 10) == RED


def test_prewarm_reaches_every_overlay_that_has_one(qapp):
    calls = []
    first, second = Filler("a", RED, calls), Filler("b", BLUE, calls)
    composite = webcam_layer.CompositeOverlay(first, object(), second, None)
    composite.prewarm(7.0)
    assert first.prewarmed == [7.0] and second.prewarmed == [7.0]


def test_none_is_left_out(qapp):
    calls = []
    only = Filler("pointer", BLUE, calls)
    composite = webcam_layer.CompositeOverlay(None, only)
    assert composite.overlays == (only,)


def test_an_overlay_that_fails_is_dropped_and_the_others_go_on(qapp):
    calls = []
    broken, pointer = Broken(), Filler("pointer", BLUE, calls)
    composite = webcam_layer.CompositeOverlay(broken, pointer)

    composite.draw(frame(), 1.0)
    composite.draw(frame(), 2.0)

    assert broken.calls == 1 and calls == [("pointer", 1.0), ("pointer", 2.0)]
    assert [(overlay, str(error)) for overlay, error in composite.failures] == [(broken, "camera glitch")]
    assert composite.overlays == (pointer,)
