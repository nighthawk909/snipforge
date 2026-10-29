"""Moving the snip around the editor by dragging: the middle mouse button with any tool, or the
Select tool on the empty area around the snip. It moves even when the snip fits the window."""
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from tests.test_editor import drag, gesture, make_canvas, send

MIDDLE = (100, 50)  # the middle of the 200 x 100 test snip


@pytest.fixture
def canvas(white_image):
    return make_canvas(white_image)


def at(canvas, scene_pt=MIDDLE) -> QPointF:
    """Where a point of the snip is in the window."""
    return QPointF(canvas.mapFromScene(QPointF(*scene_pt)))


def mouse(canvas, kind, view_pt, button=Qt.LeftButton):
    etype = {"press": QEvent.MouseButtonPress, "move": QEvent.MouseMove, "release": QEvent.MouseButtonRelease,
             "double": QEvent.MouseButtonDblClick}[kind]
    pressed = Qt.NoButton if kind == "release" else button
    event = QMouseEvent(etype, QPointF(*view_pt), canvas.viewport().mapToGlobal(QPointF(*view_pt)),
                        Qt.NoButton if kind == "move" else button, pressed, Qt.NoModifier)
    QApplication.sendEvent(canvas.viewport(), event)


def view_drag(canvas, a, b, button=Qt.LeftButton):
    mouse(canvas, "press", a, button)
    mouse(canvas, "move", ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), button)
    mouse(canvas, "move", b, button)
    mouse(canvas, "release", b, button)


def moved(before: QPointF, after: QPointF) -> tuple[int, int]:
    return round(after.x() - before.x()), round(after.y() - before.y())


def well_point(canvas) -> tuple[float, float]:
    """A spot in the window on the empty area, left of the snip."""
    left = at(canvas, (0, 50))
    return left.x() - 30, left.y()


def test_the_middle_button_drags_the_snip_with_any_tool(canvas):
    canvas.set_tool("arrow")
    before = at(canvas)
    view_drag(canvas, (before.x(), before.y()), (before.x() + 40, before.y() + 30), Qt.MiddleButton)
    assert moved(before, at(canvas)) == (40, 30)
    assert canvas.annotations() == []  # nothing drawn


def test_select_drags_the_empty_area_to_move_the_snip(canvas):
    canvas.set_tool("select")
    before = at(canvas)
    x, y = well_point(canvas)
    view_drag(canvas, (x, y), (x - 50, y + 20))
    assert moved(before, at(canvas)) == (-50, 20)


def test_select_on_the_snip_still_draws_a_selection_box(canvas):
    box = drag(canvas, "rect", (20, 20), (60, 60))
    canvas.set_tool("select")
    canvas._scene.clearSelection()
    before = at(canvas)
    gesture(canvas, (10, 10), (80, 80))
    assert box.isSelected() and moved(before, at(canvas)) == (0, 0)


def test_other_tools_still_draw_from_the_empty_area(canvas):
    before = at(canvas)
    arrow = drag(canvas, "arrow", (-20, 40), (80, 60))
    assert arrow is not None and moved(before, at(canvas)) == (0, 0)


def test_a_snip_that_fits_the_window_still_moves(canvas):
    canvas.fit_image()
    assert canvas.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    canvas.set_tool("select")
    before = at(canvas)
    x, y = well_point(canvas)
    view_drag(canvas, (x, y), (x + 120, y - 60))
    assert moved(before, at(canvas)) == (120, -60)


def test_it_never_goes_entirely_out_of_sight(canvas):
    canvas.set_tool("select")
    for _ in range(6):  # far to the right, again and again
        x, y = well_point(canvas)
        view_drag(canvas, (x, y), (x + 400, y))
    viewport = canvas.viewport().rect()
    assert at(canvas, (0, 50)).x() < viewport.right()  # its left edge is still in the window


def test_double_click_on_the_empty_area_brings_it_back(canvas):
    canvas.set_tool("select")
    x, y = well_point(canvas)
    view_drag(canvas, (x, y), (x + 100, y + 50))
    x, y = well_point(canvas)
    mouse(canvas, "press", (x, y))
    mouse(canvas, "release", (x, y))
    mouse(canvas, "double", (x, y))
    mouse(canvas, "release", (x, y))
    centre = at(canvas)
    viewport = canvas.viewport().rect()
    assert abs(centre.x() - viewport.width() / 2) <= 2 and abs(centre.y() - viewport.height() / 2) <= 2


def test_space_drag_still_moves_it(canvas):
    from PySide6.QtTest import QTest
    canvas.set_tool("arrow")
    QTest.keyPress(canvas, Qt.Key_Space)
    before = at(canvas)
    send(canvas, "press", MIDDLE)
    target = (MIDDLE[0] + 30, MIDDLE[1])
    send(canvas, "move", target)
    send(canvas, "release", target)
    QTest.keyRelease(canvas, Qt.Key_Space)
    assert moved(before, at(canvas))[0] == 30


def test_the_empty_area_shows_a_hand_to_say_it_drags(canvas):
    canvas.set_tool("select")
    x, y = well_point(canvas)
    mouse(canvas, "move", (x, y), Qt.NoButton)
    assert canvas.viewport().cursor().shape() == Qt.OpenHandCursor
    middle = at(canvas)
    mouse(canvas, "move", (middle.x(), middle.y()), Qt.NoButton)
    assert canvas.viewport().cursor().shape() == Qt.ArrowCursor


# --- review: a click on the empty area still deselects; resizing keeps the snip in the middle --
def test_a_click_on_the_empty_area_still_deselects(canvas):
    box = drag(canvas, "rect", (20, 20), (60, 60))
    canvas.set_tool("select")
    box.setSelected(True)
    x, y = well_point(canvas)
    mouse(canvas, "press", (x, y))
    mouse(canvas, "release", (x, y))
    assert not box.isSelected()


def test_resizing_the_window_keeps_the_snip_where_it_was_in_the_middle(canvas):
    canvas.fit_image()
    QApplication.processEvents()
    centre = canvas.mapToScene(canvas.viewport().rect().center())
    canvas.resize(900, 700)
    QApplication.processEvents()
    after = canvas.mapToScene(canvas.viewport().rect().center())
    assert abs(after.x() - centre.x()) <= 2 and abs(after.y() - centre.y()) <= 2


def test_a_stale_pan_never_hijacks_the_next_press(canvas):
    canvas.set_tool("select")
    x, y = well_point(canvas)
    mouse(canvas, "press", (x, y))  # its release never arrives (Alt+Tab)
    before = at(canvas)
    box = drag(canvas, "rect", (20, 20), (60, 60))  # the next press, straight on the snip
    assert box is not None and moved(before, at(canvas)) == (0, 0)
