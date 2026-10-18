"""Rotate, flip and resize the snip: the image, its markup, the crop, undo and the saved document."""
import itertools

import pytest
from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QKeyEvent, QTransform
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snipforge.editor import image_ops
from snipforge.editor.canvas import Canvas
from snipforge.editor.image_ops import ImageGeometry, render
from snipforge.editor.items import EffectItem, SpotlightItem
from snipforge.editor.style import Style
from snipforge.editor.text_items import TextItem
from tests.test_editor import drag, kinds, make_canvas

MARK = QColor("#ff0000")


@pytest.fixture
def marked(qapp):
    """200x100, white, with a red 10x10 square at (20, 10)."""
    img = QImage(200, 100, QImage.Format_ARGB32)
    img.fill(QColor("white"))
    for x in range(20, 30):
        for y in range(10, 20):
            img.setPixelColor(x, y, MARK)
    return img


def red_box(image: QImage) -> QRectF:
    xs, ys = [], []
    for x in range(image.width()):
        for y in range(image.height()):
            c = image.pixelColor(x, y)
            if c.red() > 200 and c.green() < 60 and c.blue() < 60:
                xs.append(x)
                ys.append(y)
    return QRectF(min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


def step_by_step(image: QImage, ops) -> QImage:
    for op in ops:
        if op == "cw":
            image = image.transformed(QTransform().rotate(90))
        elif op == "ccw":
            image = image.transformed(QTransform().rotate(-90))
        elif op == "h":
            image = image.flipped(Qt.Horizontal)
        else:
            image = image.flipped(Qt.Vertical)
    return image


def geometry_after(image: QImage, ops) -> ImageGeometry:
    g = ImageGeometry.of(image)
    for op in ops:
        g = (image_ops.rotate(g, op == "cw")[0] if op in ("cw", "ccw")
             else image_ops.flip(g, op == "h")[0])
    return g


# --- the geometry ---------------------------------------------------------------------
def test_any_sequence_of_turns_and_flips_matches_doing_them_one_by_one(qapp):
    image = QImage(3, 2, QImage.Format_ARGB32)
    for n, (x, y) in enumerate(itertools.product(range(3), range(2))):
        image.setPixel(x, y, 0xFF000000 | (n * 40) << 8)
    for length in (1, 2, 3, 4):
        for ops in itertools.product(("cw", "ccw", "h", "v"), repeat=length):
            assert render(image, geometry_after(image, ops)) == step_by_step(image, ops), ops


def test_points_land_on_the_same_pixels_as_the_image(marked):
    g = ImageGeometry.of(marked)
    for op in ("cw", "ccw", "h", "v"):
        new, remap = (image_ops.rotate(g, op == "cw") if op in ("cw", "ccw") else image_ops.flip(g, op == "h"))
        assert remap.transform.mapRect(QRectF(20, 10, 10, 10)) == red_box(render(marked, new)), op


def test_geometry_round_trips_and_rejects_nonsense():
    original = QSize(640, 480)
    g = ImageGeometry(640, 480, 3, True)
    assert ImageGeometry.from_dict(g.to_dict(), original) == g
    for bad in (None, [], {"turns": 4, "size": [10, 10]}, {"turns": 1.0, "size": [10, 10]},
                {"mirrored": "yes", "size": [10, 10]}, {"size": [0, 10]}, {"size": [10]},
                {"size": [100000, 10]}, {"size": [10000, 10000]}, {"size": ["10", 10]}):
        assert ImageGeometry.from_dict(bad, original) is None, bad


def test_a_capture_bigger_than_the_limits_can_still_turn(qapp):
    tall = QImage(100, image_ops.MAX_SIDE + 2000, QImage.Format_ARGB32)  # a long scrolling capture
    tall.fill(QColor("white"))
    c = Canvas(tall)
    c.rotate_image(True)
    clone = Canvas(tall)
    clone.load_document(c.to_document())
    assert clone.base_image.size().toTuple() == (image_ops.MAX_SIDE + 2000, 100)


# --- rotate and flip on the canvas ---------------------------------------------------------
def test_rotate_right_turns_the_image_and_the_export(marked):
    c = make_canvas(marked)
    c.rotate_image(clockwise=True)
    out = c.render_image()
    assert out.size().toTuple() == (100, 200)
    assert red_box(out) == QRectF(80, 20, 10, 10)
    assert c.base_image.size().toTuple() == (100, 200)


def test_markup_follows_the_image(marked):
    c = make_canvas(marked)
    rect = drag(c, "rect", (15, 5), (35, 25))  # around the red square
    arrow = drag(c, "arrow", (100, 50), (25, 15))  # points at it
    blur = drag(c, "blur", (150, 60), (190, 90))
    for op in (lambda: c.rotate_image(True), lambda: c.flip_image(True), lambda: c.rotate_image(False),
               lambda: c.flip_image(False)):
        op()
        box = red_box(c.base_image)
        assert rect.scene_rect().contains(box)
        assert QRectF(box).adjusted(-1, -1, 1, 1).contains(arrow.handle_points()["p2"])
    assert blur.scene_rect() == QRectF(150, 60, 40, 30)  # turned and flipped back to where it started


def test_text_and_stamps_stay_upright_and_centred(marked):
    c = make_canvas(marked)
    text = TextItem(Style(), "Note")
    text.setPos(40, 30)
    c._scene.addItem(text)
    center = text.sceneBoundingRect().center()
    stamp = drag(c, "stamp", (100, 40), (140, 80))
    c.rotate_image(True)
    moved = QTransform(0, 1, -1, 0, 100, 0).map(center)
    assert (text.sceneBoundingRect().center() - moved).manhattanLength() < 1
    assert text.style.font_size == Style().font_size
    assert stamp.rect().width() == stamp.rect().height() == 40


def test_callout_keeps_its_shape_and_its_pointer_target(marked):
    c = make_canvas(marked)
    callout = drag(c, "callout", (60, 40), (160, 80))
    c.commit_text()
    callout.begin_handle_drag()
    callout.drag_handle("tail", QPointF(25, 15), False)
    size = callout.rect().size()
    c.rotate_image(True)
    assert callout.rect().size() == size  # still wide, so the text still reads across
    assert QRectF(red_box(c.base_image)).adjusted(-1, -1, 1, 1).contains(callout.handle_points()["tail"])


def test_rotate_is_one_undo_step_that_restores_everything(marked):
    c = make_canvas(marked)
    rect = drag(c, "rect", (15, 5), (35, 25))
    before = (c.render_image(), rect.get_state())
    c.rotate_image(True)
    c.flip_image(True)
    assert c.undo_stack.count() == 3
    c.undo_stack.undo()
    c.undo_stack.undo()
    assert (c.render_image(), rect.get_state()) == before
    c.undo_stack.redo()
    assert c.render_image().size().toTuple() == (100, 200)


def test_crop_turns_with_the_image(marked):
    c = make_canvas(marked)
    c.set_crop_rect(QRectF(10, 0, 50, 40))
    c.rotate_image(True)
    assert c.crop_rect() == QRectF(60, 10, 40, 50)
    assert c.render_image().size().toTuple() == (40, 50)
    c.undo_stack.undo()
    assert c.crop_rect() == QRectF(10, 0, 50, 40)


def test_redactions_still_cover_what_they_hid(marked):
    c = make_canvas(marked)
    blur = drag(c, "pixelate", (10, 0), (40, 30))
    c.flip_image(True)
    out = c.render_image()
    assert blur.scene_rect() == QRectF(160, 0, 30, 30)
    assert out.pixelColor(175, 15).name() != "#ff0000"  # still hidden, from the flipped image
    assert isinstance(blur, EffectItem) and blur._base is c.base_image


def test_spotlight_dims_the_new_image_shape(marked):
    c = make_canvas(marked)
    spot = drag(c, "spotlight", (20, 10), (60, 40))
    c.rotate_image(True)
    assert isinstance(spot, SpotlightItem) and spot._image_rect == QRectF(0, 0, 100, 200)
    out = c.render_image()
    assert out.pixelColor(50, 190).lightness() < 200  # the far corner of the turned image is dimmed


def test_rotating_while_cropping_applies_the_crop_first(marked):
    c = make_canvas(marked)
    c.set_tool("crop")
    c._mask.set_crop(QRectF(10, 0, 50, 40))
    c.rotate_image(True)
    assert c.tool == "select" and not c.crop_editing()
    assert c.render_image().size().toTuple() == (40, 50)


# --- resize ----------------------------------------------------------------------------
def test_resize_scales_the_image_and_the_markup(marked):
    c = make_canvas(marked)
    rect = drag(c, "rect", (15, 5), (35, 25))
    arrow = drag(c, "arrow", (100, 50), (25, 15))
    c.resize_image(100, 50)
    assert c.render_image().size().toTuple() == (100, 50)
    assert rect.scene_rect() == QRectF(7.5, 2.5, 10, 10)
    assert rect.style.width == 2.0 and arrow.style.width == 2.0
    assert arrow.handle_points()["p2"] == QPointF(12.5, 7.5)
    c.undo_stack.undo()
    assert c.render_image().size().toTuple() == (200, 100) and rect.style.width == 4.0


def test_resize_without_keeping_the_aspect_ratio(marked):
    c = make_canvas(marked)
    stamp = drag(c, "stamp", (100, 40), (140, 80))
    c.resize_image(400, 100)
    assert c.render_image().size().toTuple() == (400, 100)
    assert stamp.rect().width() == stamp.rect().height() == 40  # stamps stay square
    assert stamp.scene_rect().center() == QPointF(240, 60)


def test_resize_targets_the_cropped_snip(marked):
    c = make_canvas(marked)
    c.set_crop_rect(QRectF(20, 10, 50, 30))
    c.resize_image(101, 61)
    assert c.render_image().size().toTuple() == (101, 61)


def test_shrinking_then_growing_back_is_the_original_pixels(marked):
    c = make_canvas(marked)
    c.resize_image(20, 10)
    c.resize_image(200, 100)
    assert c.render_image().convertToFormat(QImage.Format_ARGB32) == marked


def test_growing_never_makes_a_pixelation_finer(marked):
    style = image_ops.scaled_style("pixelate", Style(width=4).to_dict(), 3.0)
    assert (4 + 2.5 * style["width"]) >= (4 + 2.5 * 4) * 3 - 1e-6
    for sx, sy in ((4, 0.25), (1, 3), (1.1, 1.1), (0.3, 2.7)):  # either axis, and the block rounds down
        for kind, (a, b) in image_ops.STRENGTH.items():
            for strength in (1, 4, 13.7):
                width = image_ops.scaled_style(kind, Style(width=strength).to_dict(), sx, sy)["width"]
                block = int(a + b * width) if kind == "pixelate" else a + b * width
                assert block >= (a + b * strength) * max(sx, sy) - 1e-6, (kind, sx, sy, strength)
    shrunk = image_ops.scaled_style("blur", Style(width=4).to_dict(), 0.1)
    assert shrunk["width"] >= 1.0
    text = image_ops.scaled_style("text", Style(font_size=18).to_dict(), 0.1)
    assert text["font_size"] == image_ops.MIN_FONT


def test_resize_refuses_impossible_sizes(marked):
    c = make_canvas(marked)
    for w, h in ((0, 10), (10, 0), (image_ops.MAX_SIDE + 1, 10), (10000, 10000)):
        with pytest.raises(ValueError):
            c.resize_image(w, h)
    assert c.undo_stack.count() == 0


# --- the saved document ------------------------------------------------------------------
def test_document_reopens_turned_flipped_and_resized(marked):
    c = make_canvas(marked)
    drag(c, "arrow", (100, 50), (25, 15))
    drag(c, "blur", (150, 60), (190, 90))
    c.rotate_image(True)
    c.flip_image(False)
    c.resize_image(50, 100)
    doc = c.to_document()
    assert doc["image"] == {"turns": 1, "mirrored": True, "size": [50, 100]}  # flip after turn = mirror, turn
    clone = Canvas(marked)
    clone.load_document(doc)
    assert kinds(clone) == kinds(c)
    assert clone.render_image() == c.render_image()
    assert clone.undo_stack.count() == 0


def test_untouched_documents_have_no_image_entry(marked):
    c = make_canvas(marked)
    c.rotate_image(True)
    c.undo_stack.undo()
    assert "image" not in c.to_document()


@pytest.mark.parametrize("doc", [
    {"image": {"turns": 9, "size": [10, 10]}},  # damaged
    {"image": {"turns": 1, "size": [100, 200], "crop_mode": "new"}, "version": 2},  # from a newer version
])
def test_markup_from_a_document_this_version_cant_draw_is_left_alone(marked, doc):
    from snipforge.editor.canvas import UnsupportedDocument
    arrow = {"kind": "arrow", "pos": [0, 0], "style": {}, "p1": [90, 10], "p2": [10, 10]}
    c = Canvas(marked)
    with pytest.raises(UnsupportedDocument):
        c.load_document({**doc, "items": [arrow]})
    assert c.base_image.size() == marked.size() and c.annotations() == []


def test_editor_opens_such_a_document_without_autosave(qapp, tmp_path, marked):
    from snipforge.editor.window import EditorWindow
    from snipforge.library import Library
    lib = Library(tmp_path)
    entry = lib.create(marked)
    doc = {"image": {"turns": 9, "size": [10, 10]}, "items": []}
    w = EditorWindow(marked, lib, entry, doc)
    w.show()
    w.canvas.rotate_image(True)
    w.close()
    assert not (entry.path / "document.json").exists() and w.tray.saved_text() == "Autosave off"


def test_a_missing_crop_after_a_turn_is_the_whole_image(marked):
    c = Canvas(marked)
    c.load_document({"image": {"turns": 1, "mirrored": False, "size": [100, 200]}, "crop": [], "items": []})
    assert c.crop_rect() == QRectF(0, 0, 100, 200)
    assert c.render_image().size().toTuple() == (100, 200)


def test_markup_from_a_newer_version_blocks_turning(editor):
    canvas = editor.canvas
    canvas.load_document({"items": [{"kind": "laser", "pos": [0, 0], "style": {}}]})
    with pytest.raises(ValueError):
        canvas.rotate_image(True)
    editor.image_action("rotate_right")
    assert "newer SnipForge" in editor.statusBar().currentMessage()
    assert canvas.render_image().size().toTuple() == (200, 100) and canvas.undo_stack.count() == 0


def test_shrinking_never_drops_markup_from_the_saved_document(marked):
    c = make_canvas(marked)
    drag(c, "blur", (10, 10), (40, 99))
    drag(c, "arrow", (100, 50), (120, 50))
    drag(c, "stamp", (150, 20), (170, 40))
    callout = drag(c, "callout", (60, 20), (130, 60))
    callout.text.setPlainText("Hi")
    c.commit_text()
    before = sorted(s["kind"] for s in c.to_document()["items"])
    c.resize_image(20, 10)
    assert sorted(s["kind"] for s in c.to_document()["items"]) == before


def test_a_resize_to_the_same_size_does_nothing(marked):
    c = make_canvas(marked)
    c.resize_image(200, 100)
    assert c.undo_stack.count() == 0


def test_text_stays_centred_when_resized(marked):
    for size in ((100, 50), (400, 200)):
        c = make_canvas(marked)
        text = TextItem(Style(), "Centred note")
        text.setPos(60, 40)
        c._scene.addItem(text)
        k = size[0] / 200
        center = text.sceneBoundingRect().center() * k
        c.resize_image(*size)
        assert (text.sceneBoundingRect().center() - center).manhattanLength() < 2, size


# --- the panel buttons, shortcuts and the Resize dialog ------------------------------------
@pytest.fixture
def editor(qapp, tmp_path, marked):
    from snipforge.editor.window import EditorWindow
    from snipforge.library import Library
    lib = Library(tmp_path)
    w = EditorWindow(marked, lib, lib.create(marked))
    w.resize(1100, 700)
    w.show()
    QApplication.processEvents()
    yield w
    w.close()


def test_image_section_buttons_turn_and_flip(editor):
    panel = editor.panel
    assert not panel.section("image").isHidden()
    QTest.mouseClick(panel.image_button("rotate_right"), Qt.LeftButton)
    assert editor.canvas.render_image().size().toTuple() == (100, 200)
    QTest.mouseClick(panel.image_button("rotate_left"), Qt.LeftButton)
    QTest.mouseClick(panel.image_button("flip_horizontal"), Qt.LeftButton)
    assert red_box(editor.canvas.render_image()) == QRectF(170, 10, 10, 10)
    QTest.mouseClick(panel.image_button("flip_vertical"), Qt.LeftButton)
    assert red_box(editor.canvas.render_image()) == QRectF(170, 80, 10, 10)
    assert panel.image_size_text() == "200 × 100 px"


def test_ctrl_r_turns_the_snip(editor):
    editor.canvas.setFocus()
    QTest.keyClick(editor, Qt.Key_R, Qt.ControlModifier)
    assert editor.canvas.render_image().size().toTuple() == (100, 200)
    QTest.keyClick(editor, Qt.Key_R, Qt.ControlModifier | Qt.ShiftModifier)
    assert editor.canvas.render_image().size().toTuple() == (200, 100)
    assert editor.canvas.tool == "select"  # R alone is still the Rectangle tool, untouched


def test_resize_dialog_keeps_the_aspect_ratio_by_default(qapp):
    from snipforge.editor.resize_dialog import ResizeDialog
    dialog = ResizeDialog(400, 300)
    assert dialog.keep_ratio.isChecked()
    dialog.width_box.setValue(200)
    assert dialog.result_size() == (200, 150)
    dialog.set_unit("percent")
    assert dialog.width_box.value() == 50 and dialog.height_box.value() == 50
    dialog.width_box.setValue(25)
    assert dialog.result_size() == (100, 75)
    dialog.keep_ratio.setChecked(False)
    dialog.height_box.setValue(100)
    assert dialog.result_size() == (100, 300)


def test_resize_dialog_switching_units_keeps_the_exact_size(qapp):
    from snipforge.editor.resize_dialog import ResizeDialog
    dialog = ResizeDialog(333, 166)
    dialog.show()
    assert QApplication.focusWidget() in (dialog.width_box, dialog.width_box.lineEdit()) or not dialog.isActiveWindow()
    dialog.set_unit("percent")
    dialog.set_unit("pixels")
    assert dialog.result_size() == (333, 166)
    dialog.close()


def test_resize_dialog_refuses_too_big(qapp):
    from snipforge.editor.resize_dialog import ResizeDialog
    dialog = ResizeDialog(4000, 3000)
    dialog.set_unit("percent")
    dialog.width_box.setValue(800)
    assert not dialog.ok_button.isEnabled() and dialog.warning_text()


def test_resize_button_opens_the_dialog_and_applies_it(editor, monkeypatch):
    from snipforge.editor import resize_dialog
    monkeypatch.setattr(resize_dialog.ResizeDialog, "exec", lambda self: (self.width_box.setValue(100), 1)[1])
    QTest.mouseClick(editor.panel.image_button("resize"), Qt.LeftButton)
    assert editor.canvas.render_image().size().toTuple() == (100, 50)
    assert editor.tray._size.text() == "100 × 50 px"


def test_escape_still_leaves_select_after_a_turn(editor):
    editor.canvas.rotate_image(True)
    editor.canvas.set_tool("rect")
    QApplication.sendEvent(editor.canvas, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert editor.canvas.tool == "select"
