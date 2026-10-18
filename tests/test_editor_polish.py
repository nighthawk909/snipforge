"""Polish: the eyedropper by keyboard, a clean lens edge over redactions, the Beautify preview of a torn
edge matching the export, and the date stamp getting through a briefly locked meta.json."""
import pytest
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QKeyEvent, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snipforge.editor.beautify import DEFAULT_STYLE, apply_beautify
from snipforge.editor.edges import Edges
from tests.test_editor import drag, make_canvas


@pytest.fixture
def striped(qapp):
    """200x100: left half orange (#ff9500), right half teal (#2cb1bc)."""
    img = QImage(200, 100, QImage.Format_ARGB32)
    img.fill(QColor("#2cb1bc"))
    painter = QPainter(img)
    painter.fillRect(0, 0, 100, 100, QColor("#ff9500"))
    painter.end()
    return img


def key(canvas, k, mods=Qt.NoModifier):
    QApplication.sendEvent(canvas, QKeyEvent(QEvent.KeyPress, k, mods))


# --- 1. the eyedropper by keyboard -------------------------------------------------------------
def test_arrow_keys_move_the_eyedropper_and_enter_picks(striped):
    c = make_canvas(striped)
    c.set_tool("arrow")
    c.start_picking()
    key(c, Qt.Key_Right)  # starts in the middle of what you can see: (100, 50), then one right
    assert c.pick_point() == QPointF(101.5, 50.5)
    assert c._loupe.isVisible() and c._loupe.label() == "#2CB1BC"
    key(c, Qt.Key_Left, Qt.ShiftModifier)  # ten at a time
    assert c.pick_point() == QPointF(91.5, 50.5) and c._loupe.label() == "#FF9500"
    key(c, Qt.Key_Return)
    assert not c.picking() and c.tool_styles["arrow"].color == "#ff9500"


def test_the_keyboard_point_stays_on_the_snip(striped):
    c = make_canvas(striped)
    c.start_picking()
    for _ in range(30):
        key(c, Qt.Key_Up, Qt.ShiftModifier)
    assert c.pick_point().y() == 0.5
    c.set_crop_rect(QRectF(0, 0, 100, 100))
    key(c, Qt.Key_Right, Qt.ShiftModifier)  # cropped away: stays on what you can see
    for _ in range(10):
        key(c, Qt.Key_Right, Qt.ShiftModifier)
    assert c.pick_point().x() == 99.5


def test_hovering_then_using_the_keys_carries_on_from_the_pointer(striped):
    from tests.test_eyedropper import hover
    c = make_canvas(striped)
    c.start_picking()
    hover(c, (30, 20))
    key(c, Qt.Key_Down)
    assert c.pick_point() == QPointF(30.5, 21.5)


def test_the_eyedropper_button_works_without_a_mouse(qapp, tmp_path, striped):
    from snipforge.editor.window import EditorWindow
    from snipforge.library import Library
    lib = Library(tmp_path)
    w = EditorWindow(striped, lib, lib.create(striped))
    w.show()
    QApplication.processEvents()
    w.canvas.set_tool("rect")
    w.activateWindow()
    QTest.keyClick(w.panel.pick_button(), Qt.Key_Space)  # Tab to it and press Space
    QApplication.processEvents()
    assert w.canvas.picking() and "arrow keys" in w.statusBar().currentMessage().lower()
    target = QApplication.focusWidget() or w.canvas  # the keyboard goes back to the canvas
    assert target is w.canvas or not w.isActiveWindow()
    key(target, Qt.Key_Left)
    key(target, Qt.Key_Enter)
    assert w.canvas.tool_styles["rect"].color == "#ff9500"
    w.close()


# --- 2. a clean lens edge over a redaction ------------------------------------------------------
def test_a_lens_over_an_erase_shows_no_trace_of_what_was_erased(qapp):
    img = QImage(400, 300, QImage.Format_ARGB32)
    img.fill(QColor("#ffffff"))
    painter = QPainter(img)
    painter.fillRect(40, 40, 10, 10, QColor("#000000"))
    painter.end()
    c = make_canvas(img)
    drag(c, "erase", (40, 40), (50, 50))  # exactly over the black square
    lens = drag(c, "magnify", (30, 30), (60, 60))
    c.apply_style(width=1.0, shadow=False, shape="rect")
    out = c.render_image()
    inside = lens.scene_rect().toAlignedRect().adjusted(3, 3, -3, -3)
    darkest = min(out.pixelColor(x, y).lightness() for x in range(inside.left(), inside.right())
                  for y in range(inside.top(), inside.bottom()))
    assert darkest > 245  # no grey edge where the erase's border is enlarged


def test_a_lens_over_a_pixelation_matches_the_pixelation(qapp):
    img = QImage(400, 300, QImage.Format_ARGB32)
    img.fill(QColor("#ffffff"))
    painter = QPainter(img)
    painter.fillRect(38, 38, 14, 14, QColor("#000000"))
    painter.end()
    c = make_canvas(img)
    drag(c, "pixelate", (40, 40), (50, 50))  # the black square sticks out by 2 px all round
    lens = drag(c, "magnify", (30, 30), (60, 60))
    c.apply_style(width=1.0, shadow=False, shape="rect")
    out = c.render_image()
    r = lens.scene_rect()
    for sx in range(41, 49):  # inside the pixelation, every enlarged pixel is the pixelation's colour
        for sy in range(41, 49):
            lens_px = QPointF(r.left() + (sx + 0.5 - 30) * 2, r.top() + (sy + 0.5 - 30) * 2).toPoint()
            assert abs(out.pixelColor(lens_px).lightness() - out.pixelColor(sx, sy).lightness()) <= 2


# --- 3. the Beautify preview of a torn edge matches the export ------------------------------------
def test_torn_edges_in_the_beautify_preview_match_the_export(striped):
    big = striped.scaled(400, 200)
    c = make_canvas(big)
    c.set_beautify(DEFAULT_STYLE)
    c.set_edges(Edges(torn=frozenset({"bottom"})))
    c.set_zoom(1.0)
    c.centerOn(QPointF(200, 100))
    QApplication.processEvents()
    view = c.viewport().grab().toImage()
    export = apply_beautify(c.render_image(), DEFAULT_STYLE)
    pad = DEFAULT_STYLE.padding
    worst = 0
    for x in range(30, 370, 3):
        for y in range(186, 200):
            v = view.pixelColor(c.mapFromScene(QPointF(x, y)))  # a whole pixel maps exactly at 100%
            e = export.pixelColor(pad + x, pad + y)
            worst = max(worst, abs(v.red() - e.red()), abs(v.green() - e.green()), abs(v.blue() - e.blue()))
    assert worst <= 4


# --- 4. the date stamp waits out a briefly locked meta.json ----------------------------------------
@pytest.fixture
def stamped_editor(qapp, tmp_path):
    from snipforge.editor.window import EditorWindow
    from snipforge.library import Library
    from snipforge.winrects import WindowInfo
    img = QImage(1200, 400, QImage.Format_ARGB32)
    img.fill(QColor("white"))
    lib = Library(tmp_path)
    w = EditorWindow(img, lib, lib.create(img, source=WindowInfo(None, "Budget.xlsx - Excel", "Excel")))
    w.show()
    yield w
    w.close()


def stamp_text_of(editor):
    from snipforge.editor.text_items import TextItem
    return [i for i in editor.canvas.annotations() if isinstance(i, TextItem)][-1].toPlainText()


def test_a_briefly_locked_meta_file_is_tried_again(stamped_editor, monkeypatch):
    monkeypatch.setattr("snipforge.editor.window.META_RETRY_S", 0)
    library = stamped_editor._library
    real = library.read_meta
    calls = []

    def flaky(entry):
        calls.append(entry)
        if len(calls) < 3:
            raise PermissionError("being indexed")
        return real(entry)
    monkeypatch.setattr(library, "read_meta", flaky)
    stamped_editor.add_source_stamp()
    assert stamp_text_of(stamped_editor).startswith("Budget.xlsx - Excel · ") and len(calls) == 3


def test_a_meta_file_that_stays_locked_gives_the_date_and_says_why(stamped_editor, monkeypatch):
    monkeypatch.setattr("snipforge.editor.window.META_RETRY_S", 0)

    def locked(_entry):
        raise PermissionError("locked")
    monkeypatch.setattr(stamped_editor._library, "read_meta", locked)
    stamped_editor.add_source_stamp()
    assert " · " not in stamp_text_of(stamped_editor)
    assert "try again" in stamped_editor.statusBar().currentMessage().lower()


# --- review: the lens follows every change; the keys' point survives zooming and scrolling ------------
def lens_canvas(qapp):
    img = QImage(400, 300, QImage.Format_ARGB32)
    img.fill(QColor("#ffffff"))
    painter = QPainter(img)
    painter.fillRect(0, 0, 400, 300, QColor("#ffffff"))
    painter.fillRect(40, 40, 10, 10, QColor("#000000"))
    painter.fillRect(62, 40, 10, 10, QColor("#000000"))
    painter.end()
    return make_canvas(img)


def lens_matches_a_fresh_render(c):
    from snipforge.editor.canvas import Canvas
    clone = Canvas(c.base_image)
    clone.load_document(c.to_document())
    return c.render_image() == clone.render_image()


def test_the_lens_follows_redactions_moving_resizing_and_restyling(qapp):
    c = lens_canvas(qapp)
    erase = drag(c, "erase", (40, 40), (50, 50))
    blur = drag(c, "blur", (30, 30), (45, 45))
    drag(c, "magnify", (30, 30), (60, 60))
    c.render_image()  # the lens keeps its picture from here
    erase.setPos(erase.pos() + QPointF(5, 0))
    assert lens_matches_a_fresh_render(c)
    blur.set_scene_rect(QRectF(30, 30, 25, 25))
    assert lens_matches_a_fresh_render(c)
    blur.set_style(blur.style.with_changes(width=12.0))
    assert lens_matches_a_fresh_render(c)
    c.undo_stack.undo()
    assert lens_matches_a_fresh_render(c)


def test_the_lens_follows_an_erase_just_outside_it_under_a_blur(qapp):
    c = lens_canvas(qapp)
    erase = drag(c, "erase", (61, 39), (73, 51))  # just right of the lens's source, under the blur
    drag(c, "blur", (40, 30), (80, 60))
    drag(c, "magnify", (30, 30), (60, 60))
    c.render_image()
    erase.setPos(erase.pos() + QPointF(60, 60))  # away: the blur now shows the black square again
    assert lens_matches_a_fresh_render(c)


def test_zooming_keeps_the_keys_point(qapp, striped):
    from tests.test_eyedropper import hover
    big = striped.scaled(3000, 2000)
    c = make_canvas(big)
    c.set_zoom(2.0)
    c.start_picking()
    hover(c, (10, 10))  # the pointer was over the snip once...
    for _ in range(60):
        key(c, Qt.Key_Right, Qt.ShiftModifier)  # ...then the keys took over, scrolling far right
    point = c.pick_point()
    assert c.viewport().rect().contains(c.mapFromScene(point)) and c._loupe.isVisible()
    c.set_zoom(1.0)  # Ctrl+- with the keyboard
    assert c.pick_point() == point and c.viewport().rect().contains(c.mapFromScene(point))


def test_enter_first_shows_where_it_would_pick(striped):
    c = make_canvas(striped)
    c.set_tool("arrow")
    c.start_picking()
    key(c, Qt.Key_Return)
    assert c.picking() and c.pick_point() == QPointF(100.5, 50.5) and c._loupe.isVisible()
    key(c, Qt.Key_Return)
    assert not c.picking() and c.tool_styles["arrow"].color == "#2cb1bc" and c.pick_point() is None


def test_a_held_enter_picks_once_and_edits_nothing(striped):
    from snipforge.editor.style import Style
    from snipforge.editor.text_items import TextItem
    c = make_canvas(striped)
    note = TextItem(Style(), "Note")
    note.setPos(20, 20)
    c._scene.addItem(note)
    c._select([note])
    c.start_picking()
    key(c, Qt.Key_Right)
    key(c, Qt.Key_Return)
    QApplication.sendEvent(c, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier, "", True))  # held
    assert c.editing_text_item() is None


def test_torn_edges_match_the_export_on_a_big_snip_too(striped):
    big = striped.scaled(3000, 700)  # bigger than the backdrop cache: drawn smaller and scaled up
    c = make_canvas(big)
    c.set_beautify(DEFAULT_STYLE)
    c.set_edges(Edges(torn=frozenset({"bottom"})))
    c.set_zoom(1.0)
    c.centerOn(QPointF(1500, 690))
    QApplication.processEvents()
    view = c.viewport().grab().toImage()
    export = apply_beautify(c.render_image(), DEFAULT_STYLE)
    pad = DEFAULT_STYLE.padding
    gone = [x for x in range(1300, 1700, 7) if c.render_image().pixelColor(x, 699).alpha() == 0]
    assert gone
    for x in gone:
        v, e = view.pixelColor(c.mapFromScene(QPointF(x, 699))), export.pixelColor(pad + x, pad + 699)
        assert max(abs(v.red() - e.red()), abs(v.green() - e.green()), abs(v.blue() - e.blue())) <= 4
