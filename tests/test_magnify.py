"""Magnify: a lens showing an enlarged part of the snip, with a movable, resizable source and lens."""
import pytest
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QImage

from snipforge.editor.canvas import Canvas
from tests.test_editor import drag, gesture, kinds, make_canvas, send


@pytest.fixture
def dotted(qapp):
    """400x300 white with a red 10x10 square at (40, 40)."""
    img = QImage(400, 300, QImage.Format_ARGB32)
    img.fill(QColor("white"))
    for x in range(40, 50):
        for y in range(40, 50):
            img.setPixelColor(x, y, QColor("#ff0000"))
    return img


def red_area(image: QImage, rect: QRectF) -> int:
    r = rect.toAlignedRect()
    return sum(1 for x in range(r.left(), r.right() + 1) for y in range(r.top(), r.bottom() + 1)
               if image.valid(x, y) and image.pixelColor(x, y).red() > 200 and image.pixelColor(x, y).green() < 80)


def test_drag_over_an_area_puts_a_lens_beside_it(dotted):
    c = make_canvas(dotted)
    lens = drag(c, "magnify", (30, 30), (60, 60))
    assert lens.kind == "magnify" and lens.source() == QRectF(30, 30, 30, 30)
    assert lens.scene_rect().size().toTuple() == (60.0, 60.0)  # twice the size
    assert not lens.scene_rect().intersects(lens.source())
    assert QRectF(dotted.rect()).contains(lens.scene_rect())
    out = c.render_image()
    assert red_area(out, lens.scene_rect()) > 300  # the 10x10 square, enlarged about 4x in area


def test_a_click_magnifies_the_area_around_it(dotted):
    c = make_canvas(dotted)
    c.set_tool("magnify")
    send(c, "press", (45, 45))
    send(c, "release", (45, 45))
    lens = c.annotations()[0]
    assert lens.source().center() == QPointF(45, 45) and lens.source().width() == 80


def test_moving_the_lens_leaves_the_source(dotted):
    c = make_canvas(dotted)
    lens = drag(c, "magnify", (30, 30), (60, 60))
    source = lens.source()
    start = lens.scene_rect().center()
    gesture(c, (start.x(), start.y()), (start.x() + 100, start.y() + 80))
    assert lens.scene_rect().center() == start + QPointF(100, 80)
    assert lens.source() == source
    c.undo_stack.undo()
    assert lens.scene_rect().center() == start


def test_dragging_inside_the_source_moves_only_the_source(dotted):
    c = make_canvas(dotted)
    lens = drag(c, "magnify", (30, 30), (60, 60))
    where = lens.scene_rect()
    gesture(c, (45, 45), (145, 95))
    assert lens.source() == QRectF(130, 80, 30, 30) and lens.scene_rect() == where
    assert red_area(c.render_image(), lens.scene_rect()) == 0  # it shows plain white now
    c.undo_stack.undo()
    assert lens.source() == QRectF(30, 30, 30, 30)


def test_source_and_lens_resize_by_their_handles(dotted):
    c = make_canvas(dotted)
    lens = drag(c, "magnify", (30, 30), (60, 60))
    gesture(c, (60, 60), (50, 50))  # the source's bottom-right handle: show less, so enlarge more
    assert lens.source() == QRectF(30, 30, 20, 20)
    r = lens.scene_rect()
    gesture(c, (r.right(), r.bottom()), (r.right() + 40, r.bottom() + 40))
    assert lens.scene_rect().size().toTuple() == (100.0, 100.0)
    assert lens.zoom() == pytest.approx(5.0)
    assert c.undo_stack.count() == 3


def test_circle_or_box_from_the_shape(dotted):
    c = make_canvas(dotted)
    lens = drag(c, "magnify", (30, 30), (60, 60))
    corner = lens.scene_rect().topLeft() + QPointF(1, 1)  # on a box's border, outside a circle
    c.apply_style(shape="ellipse")
    assert c.render_image().pixelColor(corner.toPoint()).name() == "#ffffff"  # outside the circle
    c.apply_style(shape="rect")
    assert c.render_image().pixelColor(corner.toPoint()).name() != "#ffffff"  # the box's border


def test_magnifier_saves_reopens_and_follows_a_turn(dotted):
    c = make_canvas(dotted)
    drag(c, "magnify", (30, 30), (60, 60))
    clone = Canvas(dotted)
    clone.load_document(c.to_document())
    assert kinds(clone) == ["magnify"] and clone.render_image() == c.render_image()
    c.rotate_image(True)
    lens = c.annotations()[0]
    assert lens.source() == QRectF(240, 30, 30, 30)  # (x, y) -> (300 - y, x)
    assert red_area(c.render_image(), lens.scene_rect()) > 300


def test_the_lens_shows_the_snip_not_other_markup(dotted):
    c = make_canvas(dotted)
    drag(c, "rect", (200, 200), (260, 260))
    lens = drag(c, "magnify", (30, 30), (60, 60))
    assert red_area(c.render_image(), lens.scene_rect()) > 300


# --- review: moving with a shadow, redactions, clicks on other markup, crops -------------------
def test_moving_the_lens_redraws_it_with_its_shadow(dotted):
    c = make_canvas(dotted)
    lens = drag(c, "magnify", (30, 30), (60, 60))
    assert lens.style.shadow
    c.render_image()  # the shadow keeps a picture of the item from here
    start = lens.scene_rect().center()
    gesture(c, (start.x(), start.y()), (start.x() + 60, start.y() + 100))
    c.nudge(5, 0)
    clone = Canvas(dotted)
    clone.load_document(c.to_document())
    assert c.render_image() == clone.render_image()


def test_the_lens_never_shows_what_a_redaction_hides(dotted):
    c = make_canvas(dotted)
    drag(c, "pixelate", (35, 35), (55, 55))
    lens = drag(c, "magnify", (30, 30), (60, 60))
    out = c.render_image()
    pure_red = [(x, y) for x in range(int(lens.scene_rect().left()), int(lens.scene_rect().right()))
                for y in range(int(lens.scene_rect().top()), int(lens.scene_rect().bottom()))
                if out.pixelColor(x, y).name() == "#ff0000"]
    assert not pure_red


def test_markup_inside_the_source_still_gets_its_clicks(dotted):
    c = make_canvas(dotted)
    rect = drag(c, "rect", (12, 12), (28, 28))
    lens = drag(c, "magnify", (0, 0), (40, 40))
    c.set_tool("select")
    c._select([lens])
    gesture(c, (12, 20), (12, 120))  # on the rectangle's outline, well inside the source's frame
    assert rect.pos() == QPointF(12, 112) and lens.source() == QRectF(0, 0, 40, 40)


def test_a_lens_over_its_own_source_can_still_be_moved(dotted):
    c = make_canvas(dotted)
    lens = drag(c, "magnify", (100, 100), (300, 250))
    lens.set_scene_rect(QRectF(150, 150, 40, 30))  # shrunk inside the source
    c._select([lens])
    gesture(c, (170, 165), (170, 195))
    assert lens.scene_rect().topLeft() == QPointF(150, 180) and lens.source() == QRectF(100, 100, 200, 150)


def test_a_new_lens_stays_inside_the_crop(dotted):
    c = make_canvas(dotted)
    c.set_crop_rect(QRectF(0, 0, 200, 300))
    lens = drag(c, "magnify", (150, 100), (190, 140))
    assert QRectF(0, 0, 200, 300).contains(lens.scene_rect())


def test_the_lens_keeps_the_sources_shape(dotted):
    c = make_canvas(dotted)
    lens = drag(c, "magnify", (30, 30), (90, 50))  # a wide source
    r = lens.scene_rect()
    gesture(c, (r.right(), r.bottom()), (r.right() + 60, r.bottom() + 5))
    r = lens.scene_rect()
    assert r.width() / r.height() == pytest.approx(3.0)
    assert "r" not in lens.handle_points() and "t" not in lens.handle_points()  # corners only


def test_m_picks_magnify_and_duplicates_share_the_source(dotted):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    c = make_canvas(dotted)
    QApplication.sendEvent(c, QKeyEvent(QEvent.KeyPress, Qt.Key_M, Qt.NoModifier))
    assert c.tool == "magnify"
    lens = drag(c, "magnify", (30, 30), (60, 60))
    c.duplicate_selection()
    copy = c.selected_annotations()[0]
    assert copy is not lens and copy.source() == lens.source()
    assert copy.scene_rect().topLeft() == lens.scene_rect().topLeft() + QPointF(16, 16)
