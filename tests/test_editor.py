import pytest
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QFocusEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

from snipforge.editor.canvas import Canvas
from snipforge.editor.factory import create_item
from snipforge.editor.items import EffectItem, RectItem, StepItem, constrain_45
from snipforge.editor.style import Style
from snipforge.editor.text_items import TextItem


def make_canvas(image):
    c = Canvas(image)
    c.resize(600, 400)
    c.show()
    c.set_zoom(1.0)
    c.centerOn(QPointF(image.width() / 2, image.height() / 2))
    return c


@pytest.fixture
def canvas(white_image):
    return make_canvas(white_image)


def send(canvas, kind, scene_pt, shift=False):
    """Deliver a real mouse event to the viewport at a scene coordinate."""
    view_pt = QPointF(canvas.mapFromScene(QPointF(*scene_pt)))
    etype = {"press": QEvent.MouseButtonPress, "move": QEvent.MouseMove,
             "release": QEvent.MouseButtonRelease}[kind]
    button = Qt.NoButton if kind == "move" else Qt.LeftButton
    buttons = Qt.NoButton if kind == "release" else Qt.LeftButton
    mods = Qt.ShiftModifier if shift else Qt.NoModifier
    event = QMouseEvent(etype, view_pt, canvas.viewport().mapToGlobal(view_pt), button, buttons, mods)
    QApplication.sendEvent(canvas.viewport(), event)


def gesture(canvas, a, b, shift=False):
    send(canvas, "press", a)
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    send(canvas, "move", mid, shift)
    send(canvas, "move", b, shift)
    send(canvas, "release", b, shift)


def drag(canvas, tool, a, b, shift=False):
    canvas.set_tool(tool)
    before = set(canvas.annotations())
    gesture(canvas, a, b, shift)
    new = [i for i in canvas.annotations() if i not in before]
    return new[0] if new else None


def kinds(canvas):
    return sorted(i.kind for i in canvas.annotations())


def test_draw_undo_redo(canvas):
    drag(canvas, "arrow", (10, 10), (90, 50))
    assert kinds(canvas) == ["arrow"]
    canvas.undo_stack.undo()
    assert kinds(canvas) == []
    canvas.undo_stack.redo()
    assert kinds(canvas) == ["arrow"]


def test_tiny_drag_is_discarded(canvas):
    drag(canvas, "rect", (10, 10), (11, 11))
    assert canvas.undo_stack.count() == 0 and kinds(canvas) == []


def test_backwards_drag_normalises(canvas):
    item = drag(canvas, "rect", (80, 60), (20, 20))
    assert item.pos() == QPointF(20, 20)
    assert item.rect().size().toTuple() == (60.0, 40.0)


def test_shift_makes_square(canvas):
    item = drag(canvas, "ellipse", (10, 10), (50, 30), shift=True)
    assert item.rect().width() == item.rect().height() == 40


def test_new_item_is_selected_with_handles(canvas):
    item = drag(canvas, "rect", (10, 10), (60, 60))
    assert canvas.selected_annotations() == [item]
    assert canvas._overlay.handle_at(QPointF(60, 60), 1.0) == "br"


def test_resize_handle_and_undo(canvas):
    item = drag(canvas, "rect", (10, 10), (60, 60))
    gesture(canvas, (60, 60), (110, 90))  # still on the rect tool: grabbing a handle resizes
    assert item.scene_rect() == QRectF(10, 10, 100, 80)
    assert len(canvas.annotations()) == 1
    canvas.undo_stack.undo()
    assert item.scene_rect() == QRectF(10, 10, 50, 50)


def test_select_tool_moves_item_undoably(canvas):
    item = drag(canvas, "rect", (10, 10), (60, 60))
    canvas.set_tool("select")
    gesture(canvas, (10, 25), (50, 35))  # hollow shapes are grabbed by their outline (away from handles)
    assert item.pos() == QPointF(50, 20)
    canvas.undo_stack.undo()
    assert item.pos() == QPointF(10, 10)


def test_click_selects_and_rubber_band(canvas):
    a = drag(canvas, "rect", (10, 10), (40, 40))
    b = drag(canvas, "rect", (100, 10), (130, 40))
    canvas.set_tool("select")
    canvas._scene.clearSelection()
    gesture(canvas, (195, 95), (1, 1))  # rubber band over both, starting in open space
    assert set(canvas.selected_annotations()) == {a, b}


def test_arrow_endpoint_handles(canvas):
    item = drag(canvas, "arrow", (10, 10), (90, 50))
    assert set(item.handle_points()) == {"p1", "p2"}
    item.drag_handle("p2", QPointF(150, 10), False)
    assert item.handle_points()["p2"] == QPointF(150, 10)


def test_style_applies_to_selection_and_merges_undo(canvas):
    item = drag(canvas, "rect", (10, 10), (60, 60))
    count = canvas.undo_stack.count()
    canvas.apply_style(width=6.0)
    canvas.apply_style(width=9.0)  # slider drag: should collapse into one undo step
    assert item.style.width == 9.0
    assert canvas.undo_stack.count() == count + 1
    canvas.undo_stack.undo()
    assert item.style.width == 4.0
    assert canvas.tool_styles["rect"].width == 9.0  # next rectangle matches


def test_style_without_selection_changes_tool_default(canvas):
    canvas.set_tool("arrow")
    canvas.apply_style(color="#0a84ff")
    assert canvas.tool_styles["arrow"].color == "#0a84ff"
    assert canvas.undo_stack.count() == 0


def test_step_numbers_increment(canvas):
    canvas.set_tool("step")
    for pt in ((20, 20), (60, 20)):
        send(canvas, "press", pt)
        send(canvas, "release", pt)
    assert sorted(i.number for i in canvas.annotations() if isinstance(i, StepItem)) == [1, 2]


def test_empty_new_text_is_discarded(canvas):
    canvas.set_tool("text")
    canvas._new_text(QPointF(20, 20))
    item = next(i for i in canvas.annotations() if isinstance(i, TextItem))
    canvas._on_text_finished(item, None)
    assert kinds(canvas) == [] and canvas.undo_stack.count() == 0


def test_text_added_then_edit_is_undoable(canvas):
    canvas.set_tool("text")
    canvas._new_text(QPointF(20, 20))
    item = next(i for i in canvas.annotations() if isinstance(i, TextItem))
    item.setPlainText("Hello")
    canvas._on_text_finished(item, None)
    assert canvas.undo_stack.count() == 1
    before = item.get_state()
    item.setPlainText("Hello world")
    canvas._on_text_finished(item, before)
    canvas.undo_stack.undo()
    assert item.toPlainText() == "Hello"


def test_clearing_text_deletes_item_undoably(canvas):
    item = TextItem(Style(), "Note")
    canvas._adopt(item)
    canvas._scene.addItem(item)
    before = item.get_state()
    item.setPlainText("")
    canvas._on_text_finished(item, before)
    assert kinds(canvas) == []
    canvas.undo_stack.undo()
    assert item.toPlainText() == "Note" and kinds(canvas) == ["text"]


def test_duplicate_and_delete(canvas):
    drag(canvas, "rect", (10, 10), (60, 60))
    canvas.duplicate_selection()
    assert kinds(canvas) == ["rect", "rect"]
    canvas.delete_selection()
    assert kinds(canvas) == ["rect"]


def test_nudge_merges(canvas):
    item = drag(canvas, "rect", (10, 10), (60, 60))
    count = canvas.undo_stack.count()
    canvas.nudge(1, 0)
    canvas.nudge(1, 0)
    assert item.pos() == QPointF(12, 10)
    assert canvas.undo_stack.count() == count + 1


def test_pixelate_changes_pixels(gradient_image):
    c = make_canvas(gradient_image)
    drag(c, "pixelate", (10, 10), (90, 90))
    assert c.render_image().pixel(30, 30) != gradient_image.pixel(30, 30)


def test_crop_export_size_and_undo(canvas):
    drag(canvas, "crop", (20, 10), (120, 70))
    canvas.set_tool("select")  # leaving Crop applies it
    assert canvas.render_image().size().toTuple() == (100, 60)
    canvas.undo_stack.undo()
    assert canvas.render_image().size().toTuple() == (200, 100)


def test_handles_not_in_export(canvas, white_image):
    item = drag(canvas, "rect", (150, 50), (190, 90))
    canvas.apply_style(color="#ffffff", shadow=False)
    assert item.isSelected()
    out = canvas.render_image()
    assert out.pixelColor(190, 90).name() == "#ffffff"  # no blue handle square drawn


def test_document_roundtrip(canvas, white_image):
    drag(canvas, "arrow", (10, 10), (90, 50))
    drag(canvas, "blur", (100, 10), (150, 60))
    canvas.set_tool("step")
    canvas._add_step(QPointF(20, 80))
    text = TextItem(Style(), "Hi")
    text.setPos(120, 70)
    canvas._scene.addItem(text)
    drag(canvas, "crop", (5, 5), (195, 95))
    canvas.set_tool("select")
    doc = canvas.to_document()

    clone = Canvas(white_image)
    clone.load_document(doc)
    assert kinds(clone) == kinds(canvas)
    assert clone.crop_rect() == canvas.crop_rect()
    assert clone.render_image() == canvas.render_image()


def test_create_item_rejects_unknown_kind(white_image):
    with pytest.raises(ValueError):
        create_item({"kind": "laser", "pos": [0, 0], "style": {}}, white_image, QRectF(white_image.rect()))


def test_load_document_skips_corrupt_items(white_image):
    c = Canvas(white_image)
    good = RectItem(Style())
    good.set_scene_rect(QRectF(10, 10, 30, 30))
    c.load_document({"items": [{"kind": "rect"}, good.get_state(), {"kind": "laser", "pos": [0, 0], "style": {}}]})
    assert kinds(c) == ["rect"]


def test_redactions_are_one_undo_step(canvas):
    count = canvas.add_redactions([QRectF(10, 10, 40, 10), QRectF(10, 40, 40, 10)])
    assert count == 2 and all(isinstance(i, EffectItem) for i in canvas.annotations())
    canvas.undo_stack.undo()
    assert kinds(canvas) == []


def test_constrain_45():
    p = constrain_45(QPointF(0, 0), QPointF(100, 10))
    assert abs(p.y()) < 1e-6 and p.x() > 99


def test_style_from_dict_ignores_unknown_keys():
    assert Style.from_dict({"color": "#000000", "bogus": 1}).color == "#000000"


def test_restyle_while_typing_keeps_undo_consistent(canvas):
    canvas.set_tool("text")
    canvas._new_text(QPointF(20, 20))
    item = next(i for i in canvas.annotations() if isinstance(i, TextItem))
    item.setPlainText("ab")
    canvas.apply_style(color="#0a84ff")  # swatch click mid-edit
    item.setPlainText("abcdef")
    canvas._on_text_finished(item, None)
    assert canvas.undo_stack.count() == 1
    canvas.undo_stack.undo()
    canvas.undo_stack.redo()
    assert item.toPlainText() == "abcdef" and item.style.color == "#0a84ff"


def test_handle_dragged_past_opposite_edge_flips(canvas):
    item = drag(canvas, "rect", (100, 20), (180, 80))
    gesture(canvas, (180, 50), (70, 50))  # drag the right handle past the left edge
    assert item.scene_rect() == QRectF(70, 20, 30, 60)


def test_unknown_items_survive_save(white_image):
    c = make_canvas(white_image)
    future = {"kind": "laser", "pos": [0, 0], "style": {}, "beam": 3}
    c.load_document({"items": [future], "crop": "garbage"})
    assert future in c.to_document()["items"]
    assert c.crop_rect() == QRectF(white_image.rect())


def test_autosave_waits_while_typing(tmp_path, white_image):
    from snipforge.editor.window import EditorWindow
    from snipforge.library import Library
    lib = Library(tmp_path)
    entry = lib.create(white_image)
    w = EditorWindow(white_image, lib, entry)
    w.show()
    w.canvas.set_tool("text")
    w.canvas._new_text(QPointF(20, 20))
    w._save_to_library()
    assert w.canvas.editing_text_item() is not None  # still typing
    assert not (entry.path / "document.json").exists()
    w.close()
    assert (entry.path / "document.json").exists()


def test_space_pan_resets_on_focus_loss(canvas):
    canvas._panning = True
    canvas._apply_drag_mode()
    canvas.focusOutEvent(QFocusEvent(QEvent.FocusOut, Qt.ActiveWindowFocusReason))
    assert not canvas._panning and canvas.dragMode() != canvas.DragMode.ScrollHandDrag


# --- selecting with any tool ----------------------------------------------------------
def test_click_selects_existing_item_while_drawing_tool_active(canvas):
    arrow = drag(canvas, "arrow", (10, 50), (100, 50))
    canvas.set_tool("rect")
    send(canvas, "press", (55, 50))
    send(canvas, "release", (55, 50))
    assert canvas.selected_annotations() == [arrow]
    assert [i.kind for i in canvas.annotations()] == ["arrow"]  # nothing new drawn


def test_drag_on_selected_item_moves_it(canvas):
    arrow = drag(canvas, "arrow", (10, 50), (100, 50))
    canvas.set_tool("rect")
    send(canvas, "press", (55, 50))
    send(canvas, "release", (55, 50))
    gesture(canvas, (55, 50), (75, 70))
    assert arrow.pos() == QPointF(20, 20)
    assert len(canvas.annotations()) == 1
    canvas.undo_stack.undo()
    assert arrow.pos() == QPointF(0, 0)


def test_drag_starting_on_unselected_item_draws(canvas):
    drag(canvas, "rect", (10, 10), (190, 90))
    canvas.apply_style(fill=True)
    canvas._scene.clearSelection()
    canvas.set_tool("arrow")
    gesture(canvas, (50, 50), (150, 50))
    assert sorted(i.kind for i in canvas.annotations()) == ["arrow", "rect"]


def test_shift_click_adds_to_selection(canvas):
    a = drag(canvas, "arrow", (10, 20), (100, 20))
    b = drag(canvas, "arrow", (10, 70), (100, 70))
    canvas.set_tool("arrow")
    send(canvas, "press", (55, 20))
    send(canvas, "release", (55, 20))
    canvas._drag = None
    view_pt = QPointF(canvas.mapFromScene(QPointF(55, 70)))
    for etype, btn, btns in ((QEvent.MouseButtonPress, Qt.LeftButton, Qt.LeftButton),
                             (QEvent.MouseButtonRelease, Qt.LeftButton, Qt.NoButton)):
        QApplication.sendEvent(canvas.viewport(), QMouseEvent(etype, view_pt, view_pt, btn, btns, Qt.ShiftModifier))
    assert set(canvas.selected_annotations()) == {a, b}


def test_hollow_rect_interior_does_not_block_items_inside(canvas):
    rect = drag(canvas, "rect", (10, 10), (190, 90))
    canvas.set_tool("select")
    canvas._scene.clearSelection()
    send(canvas, "press", (100, 50))
    send(canvas, "release", (100, 50))
    assert rect not in canvas.selected_annotations()


def test_hover_highlights_item(canvas):
    arrow = drag(canvas, "arrow", (10, 50), (100, 50))
    canvas._scene.clearSelection()
    send_move = QMouseEvent(QEvent.MouseMove, QPointF(canvas.mapFromScene(QPointF(55, 50))),
                            QPointF(0, 0), Qt.NoButton, Qt.NoButton, Qt.NoModifier)
    QApplication.sendEvent(canvas.viewport(), send_move)
    assert canvas._overlay._hover is arrow


# --- callouts and stamps --------------------------------------------------------------
def test_callout_drag_creates_and_starts_editing(canvas):
    from snipforge.editor.text_items import CalloutItem
    item = drag(canvas, "callout", (20, 10), (150, 50))
    assert isinstance(item, CalloutItem)
    assert canvas.editing_text_item() is item.text
    item.text.setPlainText("Click here")
    canvas.commit_text()
    assert item.plain_text() == "Click here"
    canvas.undo_stack.undo()  # undo the text edit
    assert item.plain_text() == ""
    assert "tail" in item.handle_points()


def test_callout_click_drops_default_size(canvas):
    canvas.set_tool("callout")
    send(canvas, "press", (30, 80))
    send(canvas, "release", (30, 80))
    item = canvas.annotations()[0]
    assert item.kind == "callout" and item.rect().width() == 190


def test_callout_tail_handle_and_shape_change_roundtrip(canvas, white_image):
    from snipforge.editor.text_items import CalloutItem
    item = drag(canvas, "callout", (20, 10), (150, 50))
    canvas.commit_text()
    item.begin_handle_drag()
    item.drag_handle("tail", QPointF(180, 90), False)
    canvas.apply_style(shape="ellipse", tail=True)
    clone = make_canvas(white_image)
    clone.load_document(canvas.to_document())
    restored = clone.annotations()[0]
    assert isinstance(restored, CalloutItem) and restored.style.shape == "ellipse"
    assert restored.handle_points()["tail"] == QPointF(180, 90)


def test_callout_grows_to_fit_text(canvas):
    item = drag(canvas, "callout", (20, 10), (120, 40))
    item.text.setPlainText("A much longer note that needs several lines to fit inside the bubble")
    assert item.rect().height() > 30


def test_resizing_callout_keeps_pointer_target(canvas):
    item = drag(canvas, "callout", (20, 10), (150, 50))
    canvas.commit_text()
    tip = item.handle_points()["tail"]
    item.begin_handle_drag()
    item.drag_handle("br", QPointF(190, 70), False)
    assert item.handle_points()["tail"] == tip


def test_stamp_click_places_default_and_is_square(canvas):
    canvas.set_tool("stamp")
    canvas.apply_style(stamp="warning")
    send(canvas, "press", (100, 50))
    send(canvas, "release", (100, 50))
    stamp = canvas.annotations()[0]
    assert stamp.kind == "stamp" and stamp.style.stamp == "warning"
    assert stamp.rect().width() == stamp.rect().height() == 56
    stamp.begin_handle_drag()
    stamp.drag_handle("br", QPointF(200, 90), False)
    assert stamp.rect().width() == stamp.rect().height()


def test_stamp_changes_pixels(canvas):
    canvas.set_tool("stamp")
    send(canvas, "press", (100, 50))
    send(canvas, "release", (100, 50))
    assert canvas.render_image().pixelColor(100, 50).name() != "#ffffff"


def test_dashed_style_roundtrip(canvas, white_image):
    drag(canvas, "rect", (10, 10), (60, 60))
    canvas.apply_style(dash=True)
    clone = make_canvas(white_image)
    clone.load_document(canvas.to_document())
    assert clone.annotations()[0].style.dash is True


def test_enter_edits_selected_callout(canvas):
    from PySide6.QtGui import QKeyEvent
    item = drag(canvas, "callout", (20, 10), (150, 50))
    canvas.commit_text()
    canvas._select([item])
    QApplication.sendEvent(canvas, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    assert canvas.editing_text_item() is item.text


def test_scene_items_survive_garbage_collection(white_image):
    import gc
    src = make_canvas(white_image)
    drag(src, "arrow", (10, 10), (90, 50))
    drag(src, "callout", (100, 10), (190, 50))
    src.commit_text()
    c = make_canvas(white_image)
    c.load_document(src.to_document())
    for pt in ((50, 30), (150, 30), (5, 95)):  # hit-testing creates and drops temporary wrappers
        send(c, "move", pt)
        c._annotation_at(c.mapFromScene(QPointF(*pt)))
    gc.collect()
    kinds = sorted(type(i).__name__ for i in c.scene().items())
    assert "QGraphicsPixmapItem" in kinds and "ArrowItem" in kinds and "CalloutItem" in kinds
    assert c.render_image().pixelColor(5, 95).name() == "#ffffff"  # screenshot still there


def test_callout_bounds_include_pointer_tip(canvas):
    item = drag(canvas, "callout", (20, 10), (120, 40))
    canvas.commit_text()
    item.begin_handle_drag()
    item.drag_handle("tail", QPointF(190, 95), False)
    assert item.sceneBoundingRect().contains(QPointF(190, 95))


# --- review round 3 regressions -------------------------------------------------------
def test_undoing_callout_style_restores_size(canvas):
    item = drag(canvas, "callout", (20, 10), (150, 50))
    item.text.setPlainText("Some text in here")
    canvas.commit_text()
    size_before = item.rect().size()
    canvas._select([item])
    canvas.apply_style(font_size=30)
    canvas.apply_style(shape="ellipse")
    assert item.rect().height() > size_before.height()
    canvas.undo_stack.undo()
    canvas.undo_stack.undo()
    assert item.rect().size() == size_before
    canvas.undo_stack.redo()
    canvas.undo_stack.undo()
    assert item.rect().size() == size_before


def dbl(canvas, scene_pt):
    view_pt = QPointF(canvas.mapFromScene(QPointF(*scene_pt)))
    QApplication.sendEvent(canvas.viewport(), QMouseEvent(
        QEvent.MouseButtonDblClick, view_pt, view_pt, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))


def test_double_click_then_drag_is_undoable(canvas):
    arrow = drag(canvas, "arrow", (10, 50), (100, 50))
    canvas.set_tool("select")
    send(canvas, "press", (55, 50))
    send(canvas, "release", (55, 50))
    dbl(canvas, (55, 50))  # second press of a quick double-click
    send(canvas, "move", (65, 55))
    send(canvas, "move", (75, 60))
    send(canvas, "release", (75, 60))
    assert arrow.pos() == QPointF(20, 10)
    canvas.undo_stack.undo()
    assert arrow.pos() == QPointF(0, 0)


def test_double_click_callout_edits_text(canvas):
    item = drag(canvas, "callout", (20, 10), (150, 50))
    canvas.commit_text()
    canvas.set_tool("select")
    dbl(canvas, (80, 30))
    assert canvas.editing_text_item() is item.text


def test_escape_cancels_drawing(canvas):
    from PySide6.QtGui import QKeyEvent
    canvas.set_tool("rect")
    send(canvas, "press", (10, 10))
    send(canvas, "move", (40, 40))
    send(canvas, "move", (60, 60))
    QApplication.sendEvent(canvas, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    send(canvas, "release", (60, 60))
    assert canvas.annotations() == [] and canvas.undo_stack.count() == 0


def test_lost_release_does_not_orphan_item(canvas):
    canvas.set_tool("rect")
    send(canvas, "press", (10, 10))
    send(canvas, "move", (40, 40))
    send(canvas, "move", (60, 60))  # release never arrives
    gesture(canvas, (100, 10), (150, 60))
    assert len(canvas.annotations()) == canvas.undo_stack.count() == 1


def test_escape_cancels_resize(canvas):
    from PySide6.QtGui import QKeyEvent
    item = drag(canvas, "rect", (10, 10), (60, 60))
    send(canvas, "press", (60, 60))
    send(canvas, "move", (90, 90))
    QApplication.sendEvent(canvas, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert item.scene_rect() == QRectF(10, 10, 50, 50)


def test_right_release_does_not_end_left_drag(canvas):
    canvas.set_tool("rect")
    send(canvas, "press", (10, 10))
    send(canvas, "move", (40, 40))
    view_pt = QPointF(canvas.mapFromScene(QPointF(40, 40)))
    QApplication.sendEvent(canvas.viewport(), QMouseEvent(
        QEvent.MouseButtonRelease, view_pt, view_pt, Qt.RightButton, Qt.LeftButton, Qt.NoModifier))
    send(canvas, "move", (80, 80))
    send(canvas, "release", (80, 80))
    assert canvas.annotations()[0].scene_rect() == QRectF(10, 10, 70, 70)


def test_narrow_callout_is_clamped_and_saved(canvas):
    item = drag(canvas, "callout", (20, 10), (150, 50))
    canvas.commit_text()
    item.begin_handle_drag()
    item.drag_handle("r", QPointF(25, 30), False)
    assert item.rect().width() >= 24
    assert any(s["kind"] == "callout" for s in canvas.to_document()["items"])


def test_rect_resize_never_collapses(canvas):
    item = drag(canvas, "rect", (10, 10), (60, 60))
    item.begin_handle_drag()
    item.drag_handle("br", QPointF(11, 11), False)
    assert not item.is_degenerate()


def test_reentering_edit_keeps_undo_baseline(canvas):
    item = drag(canvas, "callout", (20, 10), (150, 50))
    item.text.setPlainText("Original")
    canvas.commit_text()
    item.start_text_editing()
    item.text.setPlainText("Half typ")
    item.start_text_editing()  # double-click again mid-edit
    item.text.setPlainText("Half typed and done")
    canvas.commit_text()
    canvas.undo_stack.undo()
    assert item.plain_text() == "Original"


def test_hit_area_extends_past_hollow_outline(canvas):
    rect = drag(canvas, "rect", (40, 30), (160, 80))
    canvas._scene.clearSelection()
    canvas.set_tool("select")
    send(canvas, "press", (34, 55))  # 6px outside the left edge
    send(canvas, "release", (34, 55))
    assert canvas.selected_annotations() == [rect]


# --- crop -----------------------------------------------------------------------------
def crop_to(canvas, a, b):
    canvas.set_tool("crop")
    gesture(canvas, a, b)


def test_crop_actually_cuts_and_hides_outside(canvas):
    crop_to(canvas, (20, 10), (120, 70))
    canvas.set_tool("select")
    assert canvas.crop_rect() == QRectF(20, 10, 100, 60)
    assert not canvas.crop_editing()
    assert canvas.scene().sceneRect().contains(QRectF(20, 10, 100, 60))
    assert not canvas.scene().sceneRect().contains(QRectF(0, 0, 200, 100))  # cut area is gone from view


def test_crop_edges_drag_back_out_to_uncut(canvas):
    crop_to(canvas, (20, 10), (120, 70))
    canvas.set_tool("select")
    canvas.set_tool("crop")  # re-enter: the whole shot is available again
    gesture(canvas, (120, 40), (180, 40))  # drag the right edge back out
    canvas.set_tool("select")
    assert canvas.crop_rect() == QRectF(20, 10, 160, 60)


def test_crop_edge_cannot_leave_the_image(canvas):
    canvas.set_tool("crop")
    gesture(canvas, (200, 50), (400, 50))  # right edge handle, dragged past the image
    assert canvas._mask.crop.right() == 200


def test_crop_cancel_discards_changes(canvas):
    from PySide6.QtGui import QKeyEvent
    crop_to(canvas, (20, 10), (120, 70))
    QApplication.sendEvent(canvas, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert canvas.crop_rect() == QRectF(0, 0, 200, 100) and canvas.tool == "select"


def test_crop_enter_applies(canvas):
    from PySide6.QtGui import QKeyEvent
    crop_to(canvas, (20, 10), (120, 70))
    QApplication.sendEvent(canvas, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    assert canvas.crop_rect() == QRectF(20, 10, 100, 60) and canvas.tool == "select"


def test_crop_show_all_then_apply_restores_full_image(canvas):
    crop_to(canvas, (20, 10), (120, 70))
    canvas.set_tool("select")
    canvas.set_tool("crop")
    canvas.reset_crop_draft()
    canvas.set_tool("select")
    assert canvas.crop_rect() == QRectF(0, 0, 200, 100)


def test_crop_move_inside(canvas):
    crop_to(canvas, (20, 10), (120, 70))
    gesture(canvas, (70, 40), (90, 50))  # drag inside the crop moves it
    canvas.set_tool("select")
    assert canvas.crop_rect() == QRectF(40, 20, 100, 60)


def test_crop_is_one_undo_step(canvas):
    crop_to(canvas, (20, 10), (120, 70))
    gesture(canvas, (120, 40), (150, 40))
    canvas.set_tool("select")
    assert canvas.undo_stack.count() == 1
    canvas.undo_stack.undo()
    assert canvas.crop_rect() == QRectF(0, 0, 200, 100)


def test_crop_geometry_helpers():
    from snipforge.editor.crop import move_crop, resize_crop
    bounds = QRectF(0, 0, 200, 100)
    assert resize_crop(QRectF(20, 10, 100, 60), "l", QPointF(130, 40), bounds).width() >= 8
    assert move_crop(QRectF(150, 50, 100, 60), QPointF(0, 0), bounds) == QRectF(100, 40, 100, 60)


# --- review round 4 regressions -------------------------------------------------------
def test_crop_snaps_to_whole_pixels(canvas):
    canvas.set_zoom(1.37)
    canvas.set_tool("crop")
    gesture(canvas, (20.4, 10.6), (120.3, 70.2))
    r = canvas._mask.crop
    assert all(float(v).is_integer() for v in (r.x(), r.y(), r.width(), r.height()))


def test_enter_mid_crop_drag_keeps_crop_and_ends_drag(canvas):
    from PySide6.QtGui import QKeyEvent
    canvas.set_tool("crop")
    send(canvas, "press", (20, 10))
    send(canvas, "move", (60, 40))
    send(canvas, "move", (120, 70))
    QApplication.sendEvent(canvas, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    send(canvas, "move", (180, 90))  # stray movement after applying must not change anything
    assert canvas._drag is None and canvas.crop_rect() == QRectF(20, 10, 100, 60)
    assert canvas.render_image().size().toTuple() == (100, 60)


def test_rechoosing_crop_keeps_adjustments(canvas):
    canvas.set_tool("crop")
    gesture(canvas, (20, 10), (120, 70))
    canvas.set_tool("crop")
    assert canvas._mask.crop == QRectF(20, 10, 100, 60)


def test_autosave_mid_crop_matches_saved_document(tmp_path, white_image):
    from snipforge.editor.window import EditorWindow
    from snipforge.library import Library
    lib = Library(tmp_path)
    entry = lib.create(white_image)
    w = EditorWindow(white_image, lib, entry)
    w.show()
    w.canvas.set_tool("crop")
    w.canvas._mask.set_crop(QRectF(10, 10, 50, 40))  # a crop in progress, not applied
    w._save_to_library()
    from PySide6.QtGui import QImage
    assert QImage(str(entry.rendered_path)).size().toTuple() == (200, 100)
    w.canvas.cancel_crop()
    w.close()


def test_hidden_annotations_are_not_selectable(canvas):
    outside = drag(canvas, "rect", (150, 60), (190, 90))
    canvas.set_tool("crop")
    gesture(canvas, (30, 20), (100, 50))  # a new crop, away from the corner handles
    canvas.set_tool("select")
    canvas._scene.clearSelection()
    canvas.select_all()
    assert outside not in canvas.selected_annotations()


# --- event dispatch through the canvas's mixins ---------------------------------------
def test_ctrl_wheel_zooms_within_limits(canvas):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QWheelEvent

    from snipforge.editor.canvas import MAX_ZOOM, MIN_ZOOM
    seen = []
    canvas.zoom_changed.connect(seen.append)
    pos = QPointF(canvas.viewport().rect().center())
    for _ in range(40):
        QApplication.sendEvent(canvas.viewport(), QWheelEvent(
            pos, canvas.viewport().mapToGlobal(pos), QPoint(0, 0), QPoint(0, 120), Qt.NoButton,
            Qt.ControlModifier, Qt.NoScrollPhase, False))
    assert seen and seen[0] > 1.0
    assert MIN_ZOOM <= canvas.zoom() <= MAX_ZOOM


def test_leaving_the_canvas_clears_the_hover(canvas):
    arrow = drag(canvas, "arrow", (10, 50), (100, 50))
    canvas._scene.clearSelection()
    hover = QMouseEvent(QEvent.MouseMove, QPointF(canvas.mapFromScene(QPointF(55, 50))), QPointF(0, 0),
                        Qt.NoButton, Qt.NoButton, Qt.NoModifier)
    QApplication.sendEvent(canvas.viewport(), hover)
    assert canvas._overlay._hover is arrow
    QApplication.sendEvent(canvas, QEvent(QEvent.Leave))
    assert canvas._overlay._hover is None


def test_space_release_ends_panning(canvas):
    from PySide6.QtGui import QKeyEvent
    QApplication.sendEvent(canvas, QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier))
    assert canvas._panning
    QApplication.sendEvent(canvas, QKeyEvent(QEvent.KeyRelease, Qt.Key_Space, Qt.NoModifier))
    assert not canvas._panning and canvas.dragMode() != canvas.DragMode.ScrollHandDrag
