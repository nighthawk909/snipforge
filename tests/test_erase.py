"""Smart erase: fill a selected area in from its surroundings, quickly, with Qt alone."""
import time

import pytest
from PySide6.QtCore import QPointF, QRect
from PySide6.QtGui import QColor, QImage, QPainter

from snipforge.editor.canvas import Canvas
from snipforge.editor.erase import fill
from tests.test_editor import drag, kinds, make_canvas


@pytest.fixture
def note(qapp):
    """240x120, a light grey page with a black "word" at (80..160, 50..70)."""
    img = QImage(240, 120, QImage.Format_ARGB32)
    img.fill(QColor("#eeeeee"))
    p = QPainter(img)
    p.fillRect(80, 50, 80, 20, QColor("#000000"))
    p.end()
    return img


def test_erasing_text_on_a_plain_background_leaves_it_plain(note):
    patch = fill(note, QRect(75, 45, 90, 30))
    assert patch.size().toTuple() == (90, 30)
    colors = {patch.pixelColor(x, y).name() for x in range(90) for y in range(30)}
    assert colors == {"#eeeeee"}


def test_a_gradient_is_filled_smoothly(qapp):
    img = QImage(200, 100, QImage.Format_ARGB32)
    for x in range(200):
        for y in range(100):
            img.setPixelColor(x, y, QColor(x, x, x))
    patch = fill(img, QRect(60, 20, 80, 60))
    row = [patch.pixelColor(x, 30).red() for x in range(80)]
    assert row[0] < row[40] < row[79]  # still dark to light
    assert abs(row[40] - 100) < 25


def test_nothing_erased_leaks_into_the_fill(qapp):
    img = QImage(100, 100, QImage.Format_ARGB32)
    img.fill(QColor("#ffffff"))
    painter = QPainter(img)
    painter.fillRect(40, 40, 20, 20, QColor("#ff0000"))
    painter.end()
    patch = fill(img, QRect(40, 40, 20, 20))
    assert all(patch.pixelColor(x, y).red() == patch.pixelColor(x, y).green() for x in range(20) for y in range(20))


def test_quick_on_a_4k_snip(qapp):
    big = QImage(3840, 2160, QImage.Format_ARGB32)
    big.fill(QColor("#336699"))
    started = time.perf_counter()
    fill(big, QRect(800, 400, 1600, 1000))
    assert time.perf_counter() - started < 1.5  # typically well under 0.2 s


def test_the_erase_tool_draws_an_editable_fill(note):
    c = make_canvas(note)
    item = drag(c, "erase", (75, 45), (165, 75))
    assert kinds(c) == ["erase"]
    out = c.render_image()
    assert {out.pixelColor(x, 60).name() for x in range(85, 155)} == {"#eeeeee"}
    c.undo_stack.undo()
    assert c.render_image().pixelColor(100, 60).name() == "#000000"
    c.undo_stack.redo()
    item.begin_handle_drag()
    item.drag_handle("r", item.scene_rect().topRight() + QPointF(30, 0), False)  # resizes like any box
    assert item.scene_rect().width() == 120


def test_erase_saves_and_follows_a_turn(note):
    c = make_canvas(note)
    drag(c, "erase", (75, 45), (165, 75))
    clone = Canvas(note)
    clone.load_document(c.to_document())
    assert clone.render_image() == c.render_image()
    c.rotate_image(True)
    out = c.render_image()
    assert out.size().toTuple() == (120, 240)
    assert {out.pixelColor(60, y).name() for y in range(85, 155)} == {"#eeeeee"}


def test_the_lens_shows_the_erased_snip(note):
    c = make_canvas(note)
    drag(c, "erase", (75, 45), (165, 75))
    lens = drag(c, "magnify", (90, 50), (110, 70))
    out = c.render_image()
    r = lens.scene_rect().toAlignedRect()
    assert not any(out.pixelColor(x, y).name() == "#000000"
                   for x in range(r.left() + 6, r.right() - 6) for y in range(r.top() + 6, r.bottom() - 6))


def test_w_picks_smart_erase(note):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    c = make_canvas(note)
    QApplication.sendEvent(c, QKeyEvent(QEvent.KeyPress, Qt.Key_W, Qt.NoModifier))
    assert c.tool == "erase"
