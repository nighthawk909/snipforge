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


@pytest.mark.parametrize("area", [QRect(40, 40, 20, 20), QRect(0, 0, 30, 30), QRect(90, 50, 30, 10), QRect(5, 5, 1, 1),
                                  QRect(0, 30, 100, 20), QRect(60, 60, 400, 400), QRect(10, 0, 3, 100)])
def test_nothing_erased_leaks_into_the_fill(qapp, area):
    """Whatever is under the area, the fill is the same, bit for bit."""
    import random
    rng = random.Random(3)
    noise = QImage(100, 100, QImage.Format_ARGB32)
    for x in range(100):
        for y in range(100):
            noise.setPixelColor(x, y, QColor(rng.randrange(256), rng.randrange(256), rng.randrange(256)))
    patches = []
    for color in ("#ffffff", "#000000", "#ff0000"):
        img = noise.copy()
        painter = QPainter(img)
        painter.fillRect(area, QColor(color))
        painter.end()
        patches.append(fill(img, area))
    assert patches[0] == patches[1] == patches[2]


def test_outside_whole_and_transparent_areas(qapp):
    img = QImage(50, 50, QImage.Format_ARGB32)
    img.fill(QColor("#123456"))
    assert fill(img, QRect(100, 100, 10, 10)).isNull()  # nothing to erase
    assert fill(img, QRect(0, 0, 50, 50)).pixelColor(25, 25).name() == "#c0c0c0"  # nothing to fill from
    clear = QImage(50, 50, QImage.Format_ARGB32)
    clear.fill(QColor(0, 0, 0, 0))
    assert fill(clear, QRect(10, 10, 20, 20)).pixelColor(5, 5).name() == "#c0c0c0"  # not black


def test_a_failed_fill_still_covers_the_area(note, monkeypatch):
    from snipforge.editor import erase
    c = make_canvas(note)
    monkeypatch.setattr(erase, "fill", lambda *_a: QImage())
    drag(c, "erase", (75, 45), (165, 75))
    assert c.render_image().pixelColor(100, 60).name() == "#c0c0c0"


def test_a_blur_over_an_erase_doesnt_bring_it_back(note):
    c = make_canvas(note)
    drag(c, "erase", (75, 45), (165, 75))
    drag(c, "blur", (70, 40), (170, 80))
    out = c.render_image()
    assert min(out.pixelColor(x, 60).lightness() for x in range(80, 160)) > 200
    c.undo_stack.undo()  # the blur's cache follows the erase
    c.undo_stack.undo()
    c.undo_stack.redo()
    c.undo_stack.redo()
    assert min(c.render_image().pixelColor(x, 60).lightness() for x in range(80, 160)) > 200


def test_grab_text_and_redact_read_the_erased_snip(note):
    c = make_canvas(note)
    drag(c, "erase", (75, 45), (165, 75))
    content = c.content_image()
    assert {content.pixelColor(x, 60).name() for x in range(80, 160)} == {"#eeeeee"}
    assert c.base_image.pixelColor(100, 60).name() == "#000000"  # the snip itself is untouched


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


def test_a_huge_area_is_filled_quickly_and_still_leaks_nothing(qapp):
    from snipforge.editor import erase
    img = QImage(3000, 2000, QImage.Format_ARGB32)
    img.fill(QColor("#ffffff"))
    painter = QPainter(img)
    painter.fillRect(200, 200, 2600, 1600, QColor("#ff0000"))
    painter.end()
    started = time.perf_counter()
    patch = fill(img, QRect(200, 200, 2600, 1600))
    assert time.perf_counter() - started < 1.5
    assert patch.size().toTuple() == (2600, 1600)
    assert 2600 * 1600 > erase.MAX_WORK
    reds = {patch.pixelColor(x, y).name() for x in range(0, 2600, 37) for y in range(0, 1600, 41)}
    assert reds == {"#ffffff"}
