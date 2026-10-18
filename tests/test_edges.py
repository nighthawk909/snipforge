"""Edge effects: a border, torn edges, and cutting out a strip."""
import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snipforge.editor.beautify import DEFAULT_STYLE, apply_beautify
from snipforge.editor.canvas import Canvas
from snipforge.editor.edges import Edges, outline
from tests.test_editor import drag, gesture, make_canvas


@pytest.fixture
def banded(qapp):
    """300x100: columns 0-99 red, 100-199 green, 200-299 blue."""
    img = QImage(300, 100, QImage.Format_ARGB32)
    for x in range(300):
        color = QColor("#ff0000") if x < 100 else QColor("#00ff00") if x < 200 else QColor("#0000ff")
        for y in range(100):
            img.setPixelColor(x, y, color)
    return img


def alpha(image, x, y):
    return image.pixelColor(x, y).alpha()


# --- border and torn edges ------------------------------------------------------------------
def test_border_is_drawn_inside_the_snip(banded):
    c = make_canvas(banded)
    c.set_edges(Edges(border=6, color="#000000"))
    out = c.render_image()
    assert out.size().toTuple() == (300, 100)  # no bigger
    assert out.pixelColor(2, 50).name() == "#000000" and out.pixelColor(150, 97).name() == "#000000"
    assert out.pixelColor(20, 50).name() == "#ff0000"


def test_torn_sides_are_transparent_in_exports(banded):
    c = make_canvas(banded)
    c.set_edges(Edges(torn=frozenset({"bottom"})))
    out = c.render_image()
    bottom_row = [alpha(out, x, 99) for x in range(300)]
    assert min(bottom_row) == 0  # torn away along the bottom
    assert all(alpha(out, x, 0) == 255 for x in range(300))  # the top is whole
    assert alpha(out, 150, 50) == 255


def test_torn_edges_are_the_same_every_time(banded):
    rect = QRectF(0, 0, 300, 100)
    a, b = outline(rect, frozenset({"top", "left"})), outline(rect, frozenset({"top", "left"}))
    assert a == b


def test_edges_are_undoable_and_saved(banded):
    c = make_canvas(banded)
    c.set_edges(Edges(border=4, color="#0a84ff", torn=frozenset({"right"})))
    assert c.undo_stack.count() == 1
    clone = Canvas(banded)
    clone.load_document(c.to_document())
    assert clone.edges() == c.edges() and clone.render_image() == c.render_image()
    c.undo_stack.undo()
    assert c.edges() == Edges() and "edges" not in c.to_document()


def test_edges_follow_the_crop(banded):
    c = make_canvas(banded)
    c.set_crop_rect(QRectF(50, 10, 200, 80))
    c.set_edges(Edges(border=3, color="#000000"))
    out = c.render_image()
    assert out.size().toTuple() == (200, 80) and out.pixelColor(1, 40).name() == "#000000"


def test_markup_near_a_torn_edge_is_torn_too(banded):
    c = make_canvas(banded)
    c.set_tool("rect")
    c.apply_style(fill=True, color="#ffffff")
    drag(c, "rect", (100, 60), (200, 99))
    c.set_edges(Edges(torn=frozenset({"bottom"})))
    out = c.render_image()
    assert min(alpha(out, x, 99) for x in range(100, 200)) == 0


def test_beautify_shows_through_torn_edges_and_is_otherwise_unchanged(banded):
    c = make_canvas(banded)
    plain = apply_beautify(c.render_image(), DEFAULT_STYLE)
    assert plain.size().toTuple() == (300 + 2 * DEFAULT_STYLE.padding, 100 + 2 * DEFAULT_STYLE.padding)
    c.set_edges(Edges(torn=frozenset({"bottom"})))
    torn = apply_beautify(c.render_image(), DEFAULT_STYLE)
    assert torn.size() == plain.size()
    pad = DEFAULT_STYLE.padding
    gone = next(x for x in range(100, 300) if alpha(c.render_image(), x, 99) == 0)  # clear of the round corners
    assert torn.pixelColor(pad + gone, pad + 99) != plain.pixelColor(pad + gone, pad + 99)  # backdrop shows


def test_damaged_edges_entry_reads_as_none(banded):
    c = Canvas(banded)
    c.load_document({"edges": {"border": "wide", "color": "nope", "torn": "all"}, "items": []})
    assert c.edges() == Edges()


# --- cut out --------------------------------------------------------------------------------
def test_cut_out_a_vertical_strip_joins_the_rest(banded):
    c = make_canvas(banded)
    c.set_tool("cutout")
    gesture(c, (100, 50), (200, 50))  # all of the green
    out = c.render_image()
    assert out.size().toTuple() == (200, 100)
    assert out.pixelColor(99, 50).name() == "#ff0000" and out.pixelColor(100, 50).name() == "#0000ff"
    c.undo_stack.undo()
    assert c.render_image().size().toTuple() == (300, 100)


def test_cut_out_a_horizontal_strip(banded):
    c = make_canvas(banded)
    c.set_tool("cutout")
    c.set_cutout_options(direction="horizontal")
    gesture(c, (50, 20), (60, 70))
    assert c.render_image().size().toTuple() == (300, 50)


def test_markup_past_the_cut_moves_with_the_image(banded):
    c = make_canvas(banded)
    arrow = drag(c, "arrow", (250, 20), (280, 80))
    rect = drag(c, "rect", (120, 20), (180, 60))  # inside the strip
    c.set_tool("cutout")
    gesture(c, (100, 50), (200, 50))
    assert arrow.handle_points()["p1"] == QPointF(150, 20)
    assert rect.scene_rect().center().x() == pytest.approx(100, abs=4)  # squeezed onto the seam
    assert rect in c.annotations()


def test_torn_seam_is_jagged(banded):
    c = make_canvas(banded)
    c.set_tool("cutout")
    c.set_cutout_options(seam="torn")
    gesture(c, (100, 50), (200, 50))
    out = c.render_image()
    assert out.size().toTuple() == (200, 100)
    colors = {out.pixelColor(x, y).name() for x in range(200) for y in range(100)}
    assert not any(QColor(c).green() > 100 and QColor(c).red() < 100 for c in colors)  # none of the strip
    edges = [next(x for x in range(90, 110) if out.pixelColor(x, y).name() != "#ff0000") for y in range(100)]
    assert len(set(edges)) > 2  # the white gap's edge zigzags
    assert max(out.pixelColor(edges[50] + d, 50).lightness() for d in range(5)) > 240  # white paper between
    assert out.pixelColor(50, 50).name() == "#ff0000" and out.pixelColor(150, 50).name() == "#0000ff"


def test_cuts_survive_turns_resizes_and_reopening(banded):
    c = make_canvas(banded)
    c.set_tool("cutout")
    gesture(c, (100, 50), (200, 50))
    c.set_tool("select")
    c.rotate_image(True)
    c.resize_image(50, 100)
    doc = c.to_document()
    clone = Canvas(banded)
    clone.load_document(doc)
    assert clone.render_image() == c.render_image()
    out = c.render_image()
    assert out.size().toTuple() == (50, 100)
    assert out.pixelColor(25, 10).name() == "#ff0000" and out.pixelColor(25, 90).name() == "#0000ff"


def test_esc_cancels_a_cut_in_progress(banded):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    from tests.test_editor import send
    c = make_canvas(banded)
    c.set_tool("cutout")
    send(c, "press", (100, 50))
    send(c, "move", (150, 50))
    QApplication.sendEvent(c, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    send(c, "release", (150, 50))
    assert c.render_image().size().toTuple() == (300, 100) and c.undo_stack.count() == 0



def test_a_turn_carries_torn_sides_round(banded):
    c = make_canvas(banded)
    c.set_edges(Edges(torn=frozenset({"bottom"})))
    c.rotate_image(True)
    assert c.edges().torn == frozenset({"left"})
    c.flip_image(True)
    assert c.edges().torn == frozenset({"right"})
    c.undo_stack.undo()
    c.undo_stack.undo()
    assert c.edges().torn == frozenset({"bottom"})


def test_a_cut_shrinks_the_crop_with_it(banded):
    c = make_canvas(banded)
    c.set_crop_rect(QRectF(50, 0, 200, 100))
    c.set_tool("cutout")
    gesture(c, (100, 50), (200, 50))
    assert c.crop_rect() == QRectF(50, 0, 100, 100)


def test_damaged_cuts_in_a_document_are_refused(banded):
    from snipforge.editor.canvas import UnsupportedDocument
    for cuts in ([["x", 50, 400, "straight"]], [["z", 1, 2, "straight"]], [["x", 5, 1, "torn"]], "x"):
        c = Canvas(banded)
        with pytest.raises(UnsupportedDocument):
            c.load_document({"image": {"turns": 0, "mirrored": False, "size": [300, 100], "cuts": cuts}})


def test_jpg_and_bmp_exports_fill_torn_edges_with_white(qapp, tmp_path, banded):
    from snipforge.editor.window import EditorWindow
    from snipforge.library import Library
    lib = Library(tmp_path / "lib")
    w = EditorWindow(banded, lib, lib.create(banded))
    w.canvas.set_edges(Edges(torn=frozenset({"bottom"})))
    gone = next(x for x in range(100, 300) if alpha(w.canvas.render_image(), x, 99) == 0)
    for name in ("snip.jpg", "snip.bmp", "snip.png"):
        w._write(tmp_path / name)
        saved = QImage(str(tmp_path / name))
        if name.endswith(".png"):
            assert saved.pixelColor(gone, 99).alpha() == 0
        else:
            assert saved.pixelColor(gone, 99).lightness() > 240, name
    w.close()


# --- the panel ---------------------------------------------------------------------------------
@pytest.fixture
def editor(qapp, tmp_path, banded):
    from snipforge.editor.window import EditorWindow
    from snipforge.library import Library
    lib = Library(tmp_path)
    w = EditorWindow(banded, lib, lib.create(banded))
    w.resize(1100, 760)
    w.show()
    QApplication.processEvents()
    yield w
    w.close()


def test_edges_section_sets_a_border_and_torn_sides(editor):
    controls = editor.panel.edge_controls()
    assert not editor.panel.section("edges").isHidden()
    QTest.mouseClick(controls.border, Qt.LeftButton)
    assert editor.canvas.edges().border > 0
    QTest.mouseClick(controls.swatches["#0a84ff"], Qt.LeftButton)
    QTest.mouseClick(controls.sides["bottom"], Qt.LeftButton)
    assert editor.canvas.edges().color == "#0a84ff" and editor.canvas.edges().torn == frozenset({"bottom"})
    controls.width_slider.setValue(12)
    controls.width_slider.setValue(14)  # a slider drag is one undo step
    assert editor.canvas.edges().border == 14
    editor.canvas.undo_stack.undo()
    assert editor.canvas.edges().border != 14 and controls.width_slider.value() != 14  # the panel follows undo
    editor.canvas.set_tool("rect")
    assert editor.panel.section("edges").isHidden()


def test_cut_out_section_chooses_direction_and_seam(editor):
    editor.canvas.set_tool("cutout")
    panel = editor.panel
    assert not panel.section("cutout").isHidden() and panel.title() == "Cut out"
    controls = panel.cutout_controls()
    QTest.mouseClick(controls.direction.button("horizontal"), Qt.LeftButton)
    QTest.mouseClick(controls.seam.button("torn"), Qt.LeftButton)
    assert editor.canvas.cutout_options == {"direction": "horizontal", "seam": "torn"}
    gesture(editor.canvas, (50, 30), (50, 60))
    assert editor.canvas.render_image().size().toTuple() == (300, 70)


def test_u_picks_cut_out(editor):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    QApplication.sendEvent(editor.canvas, QKeyEvent(QEvent.KeyPress, Qt.Key_U, Qt.NoModifier))
    assert editor.canvas.tool == "cutout"


def test_the_new_sections_fit_the_panel(qapp):
    from snipforge.editor.panel import PANEL_WIDTH, PropertiesPanel
    panel = PropertiesPanel()
    for kind in ("select", "cutout"):
        panel.load(kind, None, has_selection=False)
        assert panel.widget().minimumSizeHint().width() <= PANEL_WIDTH - 14, kind
