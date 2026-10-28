"""Properties panel 3.0: opacity and rotation for any markup, a hex color box, recent and custom colors,
a tool's default look, a mini toolbar over the selection, align and distribute, lock and hide, and a
Layers list."""
import pytest
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from snipforge.editor import colors
from snipforge.editor.canvas import document_version
from snipforge.editor.factory import create_item
from snipforge.editor.panel import PropertiesPanel
from snipforge.editor.style import DEFAULT_TOOL_STYLES, Style
from snipforge.settings import app_settings
from tests.test_editor import drag, gesture, make_canvas, send


@pytest.fixture
def page(qapp):
    img = QImage(300, 200, QImage.Format_ARGB32)
    img.fill(QColor("white"))
    return img


@pytest.fixture(autouse=True)
def clean_colors():
    for key in (colors.RECENT_SETTING, colors.PALETTE_SETTING):
        app_settings().remove(key)
    yield
    for key in (colors.RECENT_SETTING, colors.PALETTE_SETTING):
        app_settings().remove(key)


# ---------------------------------------------------------------- opacity and rotation
def test_opacity_and_rotation_are_styles_any_markup_can_take(page):
    canvas = make_canvas(page)
    rect = drag(canvas, "rect", (20, 20), (120, 100))
    assert rect.style.opacity == 100 and rect.style.rotation == 0 and rect.opacity() == 1.0
    canvas.apply_style(opacity=40, rotation=90)
    assert abs(rect.opacity() - 0.4) < 0.001 and rect.rotation() == 90
    assert rect.transformOriginPoint() == QPointF(50, 40)  # turns about its middle
    scene = rect.sceneBoundingRect()
    assert abs(scene.center().x() - 70) < 1 and abs(scene.center().y() - 60) < 1
    assert scene.height() > scene.width()  # a 100x80 box turned 90° stands on end
    state = rect.get_state()
    assert state["style"]["opacity"] == 40 and state["style"]["rotation"] == 90
    assert document_version([state]) == 3
    again = create_item(state, page, QRectF(page.rect()))
    assert again.rotation() == 90 and abs(again.opacity() - 0.4) < 0.001
    assert Style.from_dict({"opacity": 150, "rotation": 400}).opacity == 100
    assert Style.from_dict({"opacity": 150, "rotation": 400}).rotation == 40
    canvas.undo_stack.undo()
    assert rect.rotation() == 0 and rect.opacity() == 1.0
    text = drag(canvas, "arrow", (10, 150), (100, 150))
    canvas.apply_style(rotation=45)
    assert text.rotation() == 45


# ---------------------------------------------------------------- lock and hide
def test_locked_markup_stays_put_and_hidden_markup_stays_out_of_the_way(page):
    canvas = make_canvas(page)
    rect = drag(canvas, "rect", (20, 20), (120, 100))
    canvas.apply_style(fill=True)  # a hollow box is only grabbed by its outline
    canvas.set_tool("arrow")  # a drag on selected markup moves it, whatever the tool
    canvas.set_locked([rect], True)
    assert rect.locked and rect.get_state()["locked"] is True
    assert document_version([rect.get_state()]) == 3
    assert canvas.undo_stack.undoText() == "Lock"
    rect.setSelected(True)
    gesture(canvas, (70, 60), (120, 110))  # a drag on it
    assert rect.pos() == QPointF(20, 20)  # didn't move
    assert rect.handle_points() == {}  # no handles to resize it by
    canvas.delete_selection()
    assert rect.scene() is canvas._scene  # locked: not deleted either
    canvas.set_locked([rect], False)
    assert not rect.locked and rect.handle_points()
    gesture(canvas, (70, 60), (120, 110))
    assert rect.pos() == QPointF(70, 70)
    canvas.set_hidden([rect], True)
    assert rect.hidden and not rect.isVisible() and not rect.isSelected()
    assert rect.get_state()["hidden"] is True
    canvas.select_all()
    assert canvas.selected_annotations() == []  # hidden markup isn't selected by Select all
    canvas.undo_stack.undo()
    assert rect.isVisible()
    again = create_item({**rect.get_state(), "hidden": True, "locked": True}, page, QRectF(page.rect()))
    assert again.hidden and again.locked and not again.isVisible()
    menu = canvas.context_menu(canvas.mapFromScene(QPointF(90, 90)))
    labels = [a.text() for a in menu.actions()]
    assert "Lock" in labels and "Hide" in labels


# ---------------------------------------------------------------- align and distribute
def test_align_and_distribute(page):
    canvas = make_canvas(page)
    a = drag(canvas, "rect", (10, 10), (50, 50))
    b = drag(canvas, "rect", (100, 30), (160, 90))
    c = drag(canvas, "rect", (200, 60), (220, 80))
    canvas.set_tool("select")
    canvas.select_all()
    canvas.align_selection("left")
    assert a.scene_rect().left() == b.scene_rect().left() == c.scene_rect().left() == 10
    assert canvas.undo_stack.undoText() == "Align"
    canvas.align_selection("center")
    assert abs(a.scene_rect().center().x() - b.scene_rect().center().x()) < 0.01
    canvas.align_selection("bottom")
    assert a.scene_rect().bottom() == b.scene_rect().bottom() == c.scene_rect().bottom() == 90
    canvas.undo_stack.undo()
    canvas.undo_stack.undo()
    canvas.undo_stack.undo()
    assert a.scene_rect().left() == 10 and b.scene_rect().left() == 100 and c.scene_rect().top() == 60
    canvas.distribute_selection("horizontal")
    gaps = (b.scene_rect().left() - a.scene_rect().right(), c.scene_rect().left() - b.scene_rect().right())
    assert abs(gaps[0] - gaps[1]) < 0.01 and a.scene_rect().left() == 10 and c.scene_rect().right() == 220
    canvas.distribute_selection("vertical")
    gaps = (b.scene_rect().top() - a.scene_rect().bottom(), c.scene_rect().top() - b.scene_rect().bottom())
    assert abs(gaps[0] - gaps[1]) < 0.01
    canvas._scene.clearSelection()
    a.setSelected(True)
    before = a.pos()
    canvas.align_selection("right")  # one item: aligns to the snip's edge
    assert a.scene_rect().right() == 300 and before != a.pos()


# ---------------------------------------------------------------- the tool's default
def test_use_the_selection_as_the_tools_default_and_reset_it(page):
    canvas = make_canvas(page)
    rect = drag(canvas, "rect", (20, 20), (120, 100))
    canvas.apply_style(color="#123456", width=9.0)
    canvas._scene.clearSelection()
    assert canvas.tool_styles["rect"].color == "#123456"  # applying to a selection also sets the tool's
    rect.setSelected(True)
    canvas.apply_style(color="#654321")
    canvas.use_selection_as_default()
    assert canvas.tool_styles["rect"] == rect.style
    canvas.reset_tool_style("rect")
    assert canvas.tool_styles["rect"] == DEFAULT_TOOL_STYLES["rect"]
    assert rect.style.color == "#654321"  # the markup itself is untouched


# ---------------------------------------------------------------- colors: hex, recent, custom palette
def test_recent_and_custom_colors_are_kept():
    assert colors.recent() == []
    for c in ("#123456", "#abcdef", "#123456"):
        colors.remember(c)
    assert colors.recent() == ["#123456", "#abcdef"]  # newest first, each once
    for n in range(12):
        colors.remember(f"#0000{n:02x}")
    assert len(colors.recent()) == colors.MAX_RECENT
    assert colors.palette() == list(colors.DEFAULT_PALETTE)
    colors.set_palette_color(2, "#00ff00")
    assert colors.palette()[2] == "#00ff00" and colors.palette()[0] == colors.DEFAULT_PALETTE[0]
    colors.reset_palette()
    assert colors.palette() == list(colors.DEFAULT_PALETTE)
    app_settings().setValue(colors.PALETTE_SETTING, "nonsense")
    assert colors.palette() == list(colors.DEFAULT_PALETTE)
    assert colors.normalize("ABC") == "#aabbcc" and colors.normalize("#12345g") is None
    assert colors.normalize("#AABBCC") == "#aabbcc" and colors.normalize("") is None


def test_the_panels_hex_box_recent_row_and_custom_swatches(qapp):
    panel = PropertiesPanel()
    seen = []
    panel.changed.connect(seen.append)
    panel.load("rect", Style(color="#ff3b30"), has_selection=True)
    assert panel.hex_field().text() == "#ff3b30"
    panel.hex_field().setText("00ff00")
    panel.hex_field().editingFinished.emit()
    assert seen[-1] == {"color": "#00ff00"} and panel.hex_field().text() == "#00ff00"
    panel.hex_field().setText("nope")
    panel.hex_field().editingFinished.emit()
    assert seen[-1] == {"color": "#00ff00"} and panel.hex_field().text() == "#00ff00"  # kept what it had
    colors.remember("#00ff00")
    panel.load("rect", Style(color="#00ff00"), has_selection=True)
    assert [s.color for s in panel.recent_swatches()] == ["#00ff00"]
    assert panel.recent_swatches()[0].isChecked()
    colors.set_palette_color(0, "#112233")
    panel.load("rect", Style(color="#112233"), has_selection=True)
    assert panel.swatch_at(0).color == "#112233" and panel.swatch_at(0).isChecked()
    panel.load("rect", Style(), has_selection=True)
    assert not panel.swatch_at(0).isChecked()


# ---------------------------------------------------------------- the panel's new sections
def test_opacity_rotation_arrange_and_default_controls(qapp):
    panel = PropertiesPanel()
    seen, arranged, defaults = [], [], []
    panel.changed.connect(seen.append)
    panel.arrange.connect(arranged.append)
    panel.tool_default.connect(defaults.append)
    panel.load("rect", Style(opacity=70, rotation=15), has_selection=True, selected_count=1)
    assert not panel.section("look").isHidden()
    assert panel.opacity_slider().value() == 70 and panel.rotation_box().value() == 15
    panel.opacity_slider().setValue(50)
    panel.rotation_box().setValue(90)
    assert seen[-2:] == [{"opacity": 50}, {"rotation": 90}]
    assert not panel.section("arrange").isHidden()
    assert not panel.arrange_button("left").isEnabled()  # one item: align to the snip only via...
    panel.load("rect", Style(), has_selection=True, selected_count=2)
    assert panel.arrange_button("left").isEnabled() and not panel.arrange_button("h").isEnabled()
    panel.load("rect", Style(), has_selection=True, selected_count=3)
    assert panel.arrange_button("h").isEnabled()
    panel.arrange_button("top").click()
    panel.arrange_button("v").click()
    assert arranged == ["top", "distribute_vertical"]
    assert not panel.default_button("use").isHidden()
    panel.default_button("use").click()
    panel.default_button("reset").click()
    assert defaults == ["use", "reset"]
    panel.load("rect", Style(), has_selection=False)
    assert panel.default_button("use").isHidden() and not panel.default_button("reset").isHidden()
    assert panel.section("arrange").isHidden()
    panel.load("blur", Style(), has_selection=True, selected_count=1)
    assert panel.section("look").isHidden()  # a blur is never see-through or turned


# ---------------------------------------------------------------- the mini toolbar
def test_the_mini_toolbar_follows_the_selection(page):
    canvas = make_canvas(page)
    bar = canvas.mini_toolbar()
    assert not bar.isVisible()
    rect = drag(canvas, "rect", (40, 60), (140, 120))
    canvas.apply_style(fill=True)
    QApplication.processEvents()
    assert bar.isVisible()
    top = canvas.mapFromScene(QPointF(40, 60))
    assert bar.geometry().bottom() <= top.y() + 2 and bar.x() >= 0  # above the selection, on screen
    canvas.set_zoom(2.0)
    QApplication.processEvents()
    assert bar.geometry().bottom() <= canvas.mapFromScene(QPointF(40, 60)).y() + 2
    bar.button("duplicate").click()
    assert len(canvas.annotations()) == 2
    bar.button("delete").click()
    assert len(canvas.annotations()) == 1
    QApplication.processEvents()
    assert not bar.isVisible()  # nothing selected any more
    rect.setSelected(True)
    QApplication.processEvents()
    bar.button("lock").click()
    assert rect.locked and bar.button("lock").isChecked()
    bar.button("lock").click()
    assert not rect.locked
    send(canvas, "press", (90, 90))
    send(canvas, "move", (120, 120))
    assert not bar.isVisible()  # out of the way while you drag
    send(canvas, "release", (120, 120))
    QApplication.processEvents()
    assert bar.isVisible()
    canvas.set_tool("text")
    gesture(canvas, (200, 150), (200, 150))  # typing: the bar stays away so it doesn't cover the words
    QApplication.processEvents()
    assert canvas.editing_text_item() is not None and not bar.isVisible()


# ---------------------------------------------------------------- layers
def test_the_layers_list_mirrors_the_markup(page):
    from snipforge.editor.layers import LayersList

    canvas = make_canvas(page)
    a = drag(canvas, "rect", (10, 10), (50, 50))
    b = drag(canvas, "arrow", (60, 60), (120, 90))
    layers = LayersList()
    layers.attach(canvas)
    assert layers.names() == ["Arrow", "Rectangle"]  # top first
    layers.list_widget().setCurrentRow(1)
    assert canvas.selected_annotations() == [a]
    a.setSelected(False)
    b.setSelected(True)
    QApplication.processEvents()
    assert layers.list_widget().currentRow() == 0
    layers.move_current(1)  # the arrow goes under the rectangle
    assert layers.names() == ["Rectangle", "Arrow"] and b.zValue() < a.zValue()
    assert canvas.undo_stack.undoText() == "Reorder"
    layers.toggle_hidden(1)
    assert b.hidden and layers.row_hidden(1)
    layers.toggle_locked(0)
    assert a.locked and layers.row_locked(0)
    canvas.delete_selection()  # b is selected but hidden: nothing happens; delete a? it's locked
    assert len(canvas.annotations()) == 2
    canvas.undo_stack.undo()
    canvas.undo_stack.undo()
    canvas.undo_stack.undo()
    assert layers.names() == ["Arrow", "Rectangle"] and not b.hidden and not a.locked


# ---------------------------------------------------------------- the editor window
@pytest.fixture
def editor(page, tmp_path):
    from snipforge.editor.window import EditorWindow
    from snipforge.library import Library

    library = Library(tmp_path)
    window = EditorWindow(page, library, library.create(page))
    window.resize(1100, 700)
    window.show()
    yield window
    window.close()


def test_the_panels_arrange_and_default_buttons_reach_the_canvas(editor):
    canvas, panel = editor.canvas, editor.panel
    a = drag(canvas, "rect", (20, 20), (60, 60))
    b = drag(canvas, "rect", (100, 40), (180, 90))
    canvas.select_all()
    QApplication.processEvents()
    assert panel.arrange_button("left").isEnabled()  # the window told the panel how many are selected
    panel.arrange_button("left").click()
    assert a.scene_rect().left() == b.scene_rect().left()
    assert canvas.undo_stack.undoText() == "Align"
    c = drag(canvas, "rect", (240, 20), (260, 40))
    for item in (a, b, c):
        item.setSelected(True)
    QApplication.processEvents()
    panel.arrange_button("h").click()  # three: spaced evenly left to right
    assert canvas.undo_stack.undoText() == "Distribute"

    canvas.select_all()
    a.setSelected(False)
    c.setSelected(False)
    canvas.apply_style(color="#00ff00")
    panel.default_button("use").click()
    assert canvas.tool_styles["rect"].color == "#00ff00"
    panel.default_button("reset").click()
    assert canvas.tool_styles["rect"] == DEFAULT_TOOL_STYLES["rect"]


def test_a_color_picked_from_the_snip_goes_into_recent(editor):
    editor.canvas.color_picked.emit("#123456")
    assert colors.recent()[0] == "#123456"


def test_the_mini_toolbar_and_layers_follow_the_theme(editor, monkeypatch):
    rethemed = []
    monkeypatch.setattr(editor.canvas.mini_toolbar(), "retheme", lambda: rethemed.append("mini"))
    editor._retheme()
    assert rethemed == ["mini"]


def test_closing_the_editor_with_layers_shown_is_quiet(page, tmp_path):
    """The canvas's undo stack fires while the window comes down; the Layers list mustn't touch its
    deleted rows then."""
    from shiboken6 import delete

    from snipforge.editor.window import EditorWindow
    from snipforge.library import Library

    library = Library(tmp_path)
    window = EditorWindow(page, library, library.create(page))
    window.show()
    drag(window.canvas, "rect", (20, 20), (60, 60))
    layers = window.panel.layers_list()
    delete(layers.list_widget())
    layers.refresh()  # no RuntimeError
    window.canvas.undo_stack.undo()
    window.close()


# ---------------------------------------------------------------- review fixes
def test_what_hides_the_snip_never_turns_see_through_even_selected_with_other_markup(page):
    canvas = make_canvas(page)
    blur = drag(canvas, "blur", (150, 20), (250, 80))
    rect = drag(canvas, "rect", (20, 20), (120, 100))
    canvas.select_all()
    canvas.apply_style(opacity=10, rotation=30)
    assert rect.opacity() < 0.2 and rect.rotation() == 30
    assert blur.opacity() == 1.0 and blur.rotation() == 0
    assert blur.style.opacity == 100 and canvas.tool_styles["blur"].opacity == 100
    # A damaged document (or pasted markup) can't do it either
    state = {**blur.get_state(), "style": {**blur.get_state()["style"], "opacity": 5, "rotation": 45}}
    blur.set_state(state)
    assert blur.opacity() == 1.0 and blur.rotation() == 0


def test_locked_markup_stays_put_under_the_keys_and_isnt_cut_onto_the_clipboard(page):
    from PySide6.QtGui import QGuiApplication

    from snipforge.editor.canvas_clipboard import MARKUP_MIME

    canvas = make_canvas(page)
    ellipse = drag(canvas, "ellipse", (150, 20), (250, 80))
    rect = drag(canvas, "rect", (20, 20), (120, 100))
    canvas.set_locked([rect], True)
    canvas.select_all()
    before = rect.get_state()
    canvas.nudge(10, 0)
    canvas.resize_selection(10, 10)
    assert rect.get_state() == before and ellipse.scene_rect().left() == 160
    assert canvas.cut_selection()
    assert canvas.annotations() == [rect]
    import json
    pasted = json.loads(bytes(QGuiApplication.clipboard().mimeData().data(MARKUP_MIME)).decode())
    assert [s["kind"] for s in pasted["items"]] == ["ellipse"]  # Paste won't make a second rect
    notices = []
    canvas.notice.connect(notices.append)
    rect.setSelected(True)
    canvas.nudge(5, 5)
    assert rect.get_state() == before and notices


def test_turned_markup_keeps_its_place_as_its_shape_changes(page):
    canvas = make_canvas(page)
    line = drag(canvas, "line", (100, 50), (200, 50))
    line.setSelected(True)
    canvas.apply_style(rotation=30)
    start = line.mapToScene(line._p1)
    line.drag_handle("p2", QPointF(260, 120), False)  # the other end stays where it is
    assert (line.mapToScene(line._p1) - start).manhattanLength() < 0.5
    # What's on screen is what's saved: a copy from its state lands in the same place
    twin = create_item(line.get_state(), page, QRectF(page.rect()))
    assert (twin.mapToScene(twin._p2) - line.mapToScene(line._p2)).manhattanLength() < 0.5

    canvas.set_tool("text")
    gesture(canvas, (40, 150), (40, 150))
    text = canvas.editing_text_item()
    from PySide6.QtTest import QTest

    QTest.keyClicks(canvas.viewport(), "ab")
    canvas.apply_style(rotation=90)
    corner = text.mapToScene(QPointF(0, 0))
    QTest.keyClicks(canvas.viewport(), " and a much longer line")
    assert (text.mapToScene(QPointF(0, 0)) - corner).manhattanLength() < 0.5
    twin = create_item(text.get_state(), page, QRectF(page.rect()))
    assert (twin.sceneBoundingRect().center() - text.sceneBoundingRect().center()).manhattanLength() < 1


def test_turned_boxes_size_by_keys_and_line_up_by_what_shows(page):
    canvas = make_canvas(page)
    tall = drag(canvas, "rect", (20, 20), (220, 60))
    other = drag(canvas, "rect", (20, 120), (60, 160))
    tall.setSelected(True)
    other.setSelected(False)
    canvas.apply_style(rotation=90)
    assert tall.handle_points() == {}  # no handles that sit off the turned shape
    corner = tall.mapToScene(QPointF(0, 0))
    canvas.resize_selection(10, 0)
    assert (tall.mapToScene(QPointF(0, 0)) - corner).manhattanLength() < 0.5
    canvas.select_all()
    canvas.align_selection("left")
    assert abs(tall.sceneBoundingRect().left() - other.scene_rect().left()) < 1


def test_a_mirrored_snip_mirrors_turned_markup(page):
    canvas = make_canvas(page)
    line = drag(canvas, "line", (60, 100), (160, 100))
    line.setSelected(True)
    canvas.apply_style(rotation=30)
    a, b = line.mapToScene(line._p1), line.mapToScene(line._p2)
    canvas.flip_image(horizontal=True)
    item = canvas.annotations()[0]
    ends = sorted([item.mapToScene(item._p1), item.mapToScene(item._p2)], key=lambda p: p.x())
    width = page.width()
    expected = sorted([QPointF(width - a.x(), a.y()), QPointF(width - b.x(), b.y())], key=lambda p: p.x())
    for got, want in zip(ends, expected, strict=True):
        assert (got - want).manhattanLength() < 1.5


def test_the_layers_list_alt_keys_and_markup_at_the_same_height(page):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from snipforge.editor.layers import LayersList

    canvas = make_canvas(page)
    spots = [drag(canvas, "spotlight", (10 + 60 * n, 10), (50 + 60 * n, 50)) for n in range(3)]
    assert len({s.zValue() for s in spots}) == 1  # spotlights all sit at one height
    layers = LayersList()
    layers.attach(canvas)
    layers.show()
    first, second, third = layers._items
    layers.list_widget().setCurrentRow(0)
    QTest.keyClick(layers.list_widget(), Qt.Key_Down, Qt.AltModifier)
    QApplication.processEvents()
    assert canvas.undo_stack.undoText() == "Reorder"
    assert layers._items == [second, first, third]  # one row down, not past the third too
    assert layers.list_widget().currentRow() == 1


def test_the_layers_list_rebuilds_once_per_change_and_not_while_hidden(page, monkeypatch):
    from snipforge.editor.layers import LayersList

    canvas = make_canvas(page)
    layers = LayersList()
    layers.attach(canvas)
    builds = []
    real = LayersList.refresh
    monkeypatch.setattr(LayersList, "refresh", lambda self: builds.append(1) or real(self))
    for n in range(5):
        drag(canvas, "rect", (10 + n * 50, 10), (40 + n * 50, 40))
    QApplication.processEvents()
    assert builds == []  # not shown: nothing built
    layers.show()
    QApplication.processEvents()
    assert len(builds) == 1 and len(layers.names()) == 5
    canvas.select_all()
    canvas.nudge(1, 0)
    canvas.nudge(1, 0)
    QApplication.processEvents()
    assert len(builds) == 2  # two nudges, one rebuild
