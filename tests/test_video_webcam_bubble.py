"""snipforge/video/webcam_bubble.py: the camera's bubble on screen, which the person moves and
shapes, and which tells the frame thread where it is."""
import dataclasses
import threading

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QContextMenuEvent, QImage, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication, QMenu

from snipforge.video import webcam_bubble
from snipforge.video.webcam_bubble import BubbleSnapshot, WebcamBubble
from snipforge.video.webcam_layer import CIRCLE, RECTANGLE, ROUNDED

WHITE, RED, BLUE = "#ffffff", "#ff0000", "#0000ff"


@pytest.fixture
def excluded(monkeypatch):
    windows = []
    monkeypatch.setattr(webcam_bubble, "exclude_from_capture", windows.append)
    return windows


@pytest.fixture
def bubble(qapp, excluded):
    widget = WebcamBubble()
    widget.move(100, 80)
    yield widget
    widget.close()


def camera_picture() -> QImage:
    image = QImage(80, 60, QImage.Format_RGB32)
    image.fill(QColor(BLUE))
    painter = QPainter(image)
    painter.fillRect(0, 0, 40, 60, QColor(RED))
    painter.end()
    return image


def rendered(widget) -> QImage:
    image = QImage(widget.size(), QImage.Format_ARGB32)
    image.fill(QColor(WHITE))
    widget.render(image)
    return image


def mouse(widget, kind, local: QPoint, global_pos: QPoint, button=Qt.LeftButton):
    etype = {"press": QEvent.MouseButtonPress, "move": QEvent.MouseMove, "release": QEvent.MouseButtonRelease}[kind]
    buttons = Qt.NoButton if kind == "release" else button
    event = QMouseEvent(etype, QPointF(local), QPointF(global_pos), Qt.NoButton if kind == "move" else button,
                        buttons, Qt.NoModifier)
    QApplication.sendEvent(widget, event)


# The window


def test_it_never_takes_focus_and_floats_on_top(bubble):
    flags = bubble.windowFlags()
    for flag in (Qt.WindowDoesNotAcceptFocus, Qt.WindowStaysOnTopHint, Qt.FramelessWindowHint, Qt.Tool):
        assert flags & flag == flag
    assert bubble.testAttribute(Qt.WA_ShowWithoutActivating) and bubble.focusPolicy() == Qt.NoFocus


def test_it_is_kept_out_of_captures_when_shown(bubble, excluded):
    bubble.show()
    assert excluded == [bubble]


def test_a_medium_circle_by_default(bubble):
    assert (bubble.shape(), bubble.bubble_size()) == (CIRCLE, webcam_bubble.MEDIUM)
    assert bubble.size() == QSize(192, 192)


@pytest.mark.parametrize(("shape", "size", "expected"), [
    (CIRCLE, webcam_bubble.SMALL, QSize(128, 128)), (CIRCLE, webcam_bubble.LARGE, QSize(288, 288)),
    (ROUNDED, webcam_bubble.MEDIUM, QSize(256, 192)), (RECTANGLE, webcam_bubble.SMALL, QSize(171, 128))])
def test_each_shape_and_size(qapp, excluded, shape, size, expected):
    widget = WebcamBubble(shape=shape, size=size)
    assert widget.size() == expected


def test_unknown_shapes_and_sizes_are_refused(qapp, excluded):
    with pytest.raises(ValueError):
        WebcamBubble(shape="star")
    with pytest.raises(ValueError):
        WebcamBubble(size="huge")


def test_a_new_shape_keeps_the_bubble_centred_where_it_was(bubble):
    centre = bubble.geometry().center()
    changed = []
    bubble.shape_changed.connect(changed.append)

    bubble.set_shape(ROUNDED)

    assert bubble.shape() == ROUNDED and bubble.size() == QSize(256, 192)
    assert (bubble.geometry().center() - centre).manhattanLength() <= 1
    assert changed == [ROUNDED]


def test_a_new_size_keeps_the_bubble_centred_where_it_was(bubble):
    centre = bubble.geometry().center()
    changed = []
    bubble.size_changed.connect(changed.append)

    bubble.set_bubble_size(webcam_bubble.LARGE)

    assert bubble.size() == QSize(288, 288) and changed == [webcam_bubble.LARGE]
    assert (bubble.geometry().center() - centre).manhattanLength() <= 1


# Moving it


def test_dragging_moves_the_bubble_and_says_where_it_went(bubble):
    bubble.show()
    start = bubble.pos()
    moves = []
    bubble.moved.connect(moves.append)
    grab = QPoint(20, 20)
    origin = bubble.mapToGlobal(grab)

    mouse(bubble, "press", grab, origin)
    mouse(bubble, "move", grab, origin + QPoint(30, 10))
    mouse(bubble, "move", grab, origin + QPoint(60, 40))
    mouse(bubble, "release", grab, origin + QPoint(60, 40))

    assert bubble.pos() == start + QPoint(60, 40)
    assert moves == [bubble.area_rect()]  # once, when the drag ends


def test_moving_without_a_press_does_nothing(bubble):
    bubble.show()
    start = bubble.pos()
    mouse(bubble, "move", QPoint(5, 5), bubble.mapToGlobal(QPoint(50, 50)), button=Qt.NoButton)
    assert bubble.pos() == start


def test_a_right_drag_doesnt_move_it(bubble):
    bubble.show()
    start = bubble.pos()
    origin = bubble.mapToGlobal(QPoint(10, 10))
    mouse(bubble, "press", QPoint(10, 10), origin, button=Qt.RightButton)
    mouse(bubble, "move", QPoint(10, 10), origin + QPoint(40, 0), button=Qt.RightButton)
    assert bubble.pos() == start


def test_move_physical_places_the_top_left_on_that_monitor(bubble):
    screen = QApplication.primaryScreen().geometry()
    bubble.move_physical(screen.x() + 30, screen.y() + 40)  # the test screen isn't scaled
    assert bubble.pos() == QPoint(screen.x() + 30, screen.y() + 40)


def test_a_position_on_a_monitor_thats_gone_goes_to_the_main_ones_corner(bubble):
    bubble.move_physical(-50000, -50000)
    available = QApplication.primaryScreen().availableGeometry()
    assert available.contains(bubble.geometry())
    assert available.bottomRight().x() - bubble.geometry().right() == webcam_bubble.MARGIN


# Physical pixels


def test_physical_pixels_keep_the_monitors_top_left_and_scale_within_it():
    second = QRect(1920, 0, 1280, 720)  # a 1920x1080 physical monitor at 150%
    assert webcam_bubble.to_physical(QRect(2020, 100, 192, 192), second, 1.5) == QRect(2070, 150, 288, 288)
    assert webcam_bubble.to_physical(QRect(10, 20, 30, 40), QRect(0, 0, 1920, 1080), 1.0) == QRect(10, 20, 30, 40)


def test_a_physical_point_maps_back_to_the_monitor_its_on():
    screens = [(QRect(0, 0, 1920, 1080), 1.0), (QRect(1920, 0, 1280, 720), 1.5)]
    assert webcam_bubble.to_logical(QPoint(2070, 150), screens) == QPoint(2020, 100)
    assert webcam_bubble.to_logical(QPoint(1000, 500), screens) == QPoint(1000, 500)
    assert webcam_bubble.to_logical(QPoint(3800, 1000), screens) == QPoint(3173, 667)
    assert webcam_bubble.to_logical(QPoint(5000, 100), screens) is None


def test_area_rect_is_in_physical_pixels(bubble):
    screen = bubble.screen()
    expected = webcam_bubble.to_physical(bubble.geometry(), screen.geometry(), screen.devicePixelRatio())
    assert bubble.area_rect() == expected and bubble.area_rect().size() == QSize(192, 192)


# The snapshot the frame thread reads


def test_no_snapshot_until_shown_and_none_once_hidden(bubble):
    assert bubble.snapshot() is None
    bubble.show()
    assert bubble.snapshot() == BubbleSnapshot((100, 80, 192, 192), CIRCLE)
    bubble.hide()
    assert bubble.snapshot() is None


def test_the_snapshot_follows_moves_sizes_and_shapes(bubble):
    bubble.show()
    bubble.move(300, 200)
    assert bubble.snapshot().rect == (300, 200, 192, 192)
    bubble.set_bubble_size(webcam_bubble.SMALL)
    assert bubble.snapshot().rect[2:] == (128, 128)
    bubble.set_shape(RECTANGLE)
    assert bubble.snapshot() == BubbleSnapshot(bubble.area_rect().getRect(), RECTANGLE)


def test_a_snapshot_cant_be_changed_by_whoever_reads_it(bubble):
    bubble.show()
    with pytest.raises(dataclasses.FrozenInstanceError):
        bubble.snapshot().shape = RECTANGLE


def test_another_thread_only_ever_sees_whole_snapshots(bubble):
    bubble.show()
    seen, done = [], threading.Event()

    def read():
        while not done.is_set():
            seen.append(bubble.snapshot())

    reader = threading.Thread(target=read)
    reader.start()
    try:
        for step in range(150):
            bubble.move(step, step)
            bubble.set_shape((CIRCLE, ROUNDED)[step % 2])
    finally:
        done.set()
        reader.join(5)

    widths = {CIRCLE: 192, ROUNDED: 256}
    assert seen
    for snap in seen:
        x, y, width, height = snap.rect
        assert width == widths[snap.shape] and height == 192  # shape and size always belong together


# The right-click menu


def actions(menu: QMenu) -> dict[str, object]:
    return {action.text(): action for action in menu.actions() if not action.isSeparator() and action.text()}


def test_the_menu_offers_each_shape_and_size_with_the_current_ones_ticked(bubble):
    found = actions(bubble.context_menu())
    for name in ("Circle", "Rounded rectangle", "Rectangle", "Small", "Medium", "Large"):
        assert name in found
    ticked = sorted(name for name, action in found.items() if action.isChecked())
    assert ticked == ["Circle", "Medium"]


def test_choosing_from_the_menu_changes_shape_and_size(bubble):
    found = actions(bubble.context_menu())
    found["Rectangle"].trigger()
    actions(bubble.context_menu())["Large"].trigger()
    assert (bubble.shape(), bubble.bubble_size()) == (RECTANGLE, webcam_bubble.LARGE)


def test_a_right_click_opens_the_menu_without_waiting_on_it(bubble):
    bubble.show()
    QApplication.sendEvent(bubble, QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(5, 5), bubble.mapToGlobal(
        QPoint(5, 5))))
    menus = [menu for menu in bubble.findChildren(QMenu) if menu.isVisible()]
    try:
        assert len(menus) == 1 and "Circle" in actions(menus[0])
    finally:
        for menu in menus:
            menu.close()


# What it shows


def test_the_camera_is_shown_mirrored_inside_a_circle(bubble):
    bubble.set_image(camera_picture())
    image = rendered(bubble)
    assert image.pixelColor(40, 96).name() == BLUE and image.pixelColor(150, 96).name() == RED
    assert image.pixelColor(3, 3).name() == WHITE  # outside the circle: see-through


def test_before_the_first_picture_it_shows_a_dark_placeholder(bubble):
    image = rendered(bubble)
    centre = image.pixelColor(96, 60)
    assert centre.lightness() < 100 and image.pixelColor(3, 3).name() == WHITE


def test_set_image_none_goes_back_to_the_placeholder(bubble):
    bubble.set_image(camera_picture())
    bubble.set_image(None)
    assert rendered(bubble).pixelColor(40, 96).name() not in (RED, BLUE)


class FakeCamera(QObject):
    frame = Signal(QImage)


def test_pictures_from_the_camera_are_shown(qapp, excluded):
    camera = FakeCamera()
    widget = WebcamBubble(camera)
    camera.frame.emit(camera_picture())
    assert rendered(widget).pixelColor(40, 96).name() == BLUE
