"""Moving, resizing and aiming markup: whatever tool is active, markup on the snip is easy to grab."""
import sys

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent
from PySide6.QtWidgets import QApplication

from tests.test_editor import drag, gesture, make_canvas, send


def alt_gesture(canvas, a, b):
    """A drag with Alt held: draw over markup instead of moving it."""
    steps = ((QEvent.MouseButtonPress, a, Qt.LeftButton, Qt.LeftButton),
             (QEvent.MouseMove, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), Qt.NoButton, Qt.LeftButton),
             (QEvent.MouseMove, b, Qt.NoButton, Qt.LeftButton),
             (QEvent.MouseButtonRelease, b, Qt.LeftButton, Qt.NoButton))
    for etype, pt, button, buttons in steps:
        view_pt = QPointF(canvas.mapFromScene(QPointF(*pt)))
        event = QMouseEvent(etype, view_pt, canvas.viewport().mapToGlobal(view_pt), button, buttons,
                            Qt.AltModifier)
        QApplication.sendEvent(canvas.viewport(), event)


def callout(canvas, a=(20, 10), b=(150, 50), text="Note"):
    item = drag(canvas, "callout", a, b)
    item.text.setPlainText(text)
    return item


def test_drawing_tool_drags_unselected_markup_instead_of_drawing(qapp, white_image):
    canvas = make_canvas(white_image)
    rect = drag(canvas, "rect", (10, 10), (190, 90))
    canvas.apply_style(fill=True)
    canvas._scene.clearSelection()
    canvas.set_tool("arrow")
    gesture(canvas, (50, 50), (80, 60))
    assert [i.kind for i in canvas.annotations()] == ["rect"]  # nothing new drawn
    assert rect.pos() == QPointF(40, 20)
    assert canvas.selected_annotations() == [rect]
    canvas.undo_stack.undo()
    assert rect.pos() == QPointF(10, 10)


def test_alt_drag_draws_over_markup(qapp, white_image):
    canvas = make_canvas(white_image)
    rect = drag(canvas, "rect", (10, 10), (190, 90))
    canvas.apply_style(fill=True)
    canvas._scene.clearSelection()
    canvas.set_tool("arrow")
    alt_gesture(canvas, (50, 50), (150, 50))
    assert sorted(i.kind for i in canvas.annotations()) == ["arrow", "rect"]
    assert rect.pos() == QPointF(10, 10)


def test_callout_tool_drags_a_callout(qapp, white_image):
    canvas = make_canvas(white_image)
    item = callout(canvas)
    canvas.commit_text()
    canvas._scene.clearSelection()
    gesture(canvas, (40, 20), (70, 40))  # the callout tool is still on
    assert item.pos() == QPointF(50, 30)
    assert canvas.editing_text_item() is None
    assert [i.kind for i in canvas.annotations()] == ["callout"]


def test_callout_tool_click_still_edits_the_text(qapp, white_image):
    canvas = make_canvas(white_image)
    item = callout(canvas)
    canvas.commit_text()
    canvas._scene.clearSelection()
    send(canvas, "press", (80, 30))
    send(canvas, "release", (80, 30))
    assert canvas.editing_text_item() is item.text


def test_text_tool_drags_text(qapp, white_image):
    canvas = make_canvas(white_image)
    canvas.set_tool("text")
    send(canvas, "press", (40, 40))
    send(canvas, "release", (40, 40))
    text = canvas.annotations()[0]
    text.setPlainText("Hello there")
    canvas.commit_text()
    start = QPointF(text.pos())
    middle = text.sceneBoundingRect().center()
    gesture(canvas, (middle.x(), middle.y()), (middle.x() + 30, middle.y() + 20))
    assert text.pos() == start + QPointF(30, 20)
    assert canvas.editing_text_item() is None


def test_resize_handle_works_while_typing_in_a_new_callout(qapp, white_image):
    canvas = make_canvas(white_image)
    item = callout(canvas)
    assert canvas.editing_text_item() is item.text  # a new callout starts in typing mode
    gesture(canvas, (150, 50), (190, 80))
    assert item.scene_rect().bottomRight() == QPointF(190, 80)
    assert item.plain_text() == "Note"


def test_pointer_handle_works_while_typing_in_a_new_callout(qapp, white_image):
    canvas = make_canvas(white_image)
    item = callout(canvas)
    tip = item.handle_points()["tail"]
    gesture(canvas, (tip.x(), tip.y()), (tip.x() + 60, tip.y() + 10))
    assert item.handle_points()["tail"] == tip + QPointF(60, 10)


def test_dragging_a_typing_callout_by_its_edge_moves_it(qapp, white_image):
    canvas = make_canvas(white_image)
    item = callout(canvas, (20, 10), (150, 90))
    assert item.text.sceneBoundingRect().top() > 22  # the press below is above the text...
    gesture(canvas, (85, 22), (115, 42))  # ...and clear of the top handle
    assert item.pos() == QPointF(50, 30)
    assert item.plain_text() == "Note"
    canvas.undo_stack.undo()
    assert item.pos() == QPointF(20, 10)


def test_pointer_can_be_grabbed_along_its_length(qapp, white_image):
    canvas = make_canvas(white_image)
    item = callout(canvas)
    canvas.commit_text()
    tip, box = item.handle_points()["tail"], item.scene_rect()
    along = box.center() + (tip - box.center()) * 0.75  # on the pointer, well away from the dot at its tip
    gesture(canvas, (along.x(), along.y()), (along.x() + 40, along.y()))
    assert item.handle_points()["tail"] == tip + QPointF(40, 0)
    assert item.scene_rect() == box  # the box stayed put
    canvas.undo_stack.undo()
    assert item.handle_points()["tail"] == tip


def test_handles_are_easy_to_hit(qapp, white_image):
    canvas = make_canvas(white_image)
    drag(canvas, "rect", (20, 20), (120, 80))
    assert canvas._overlay.handle_at(QPointF(129, 89), 1.0) == "br"  # 9 px off the corner


def test_nearest_handle_wins_on_small_markup(qapp, white_image):
    canvas = make_canvas(white_image)
    drag(canvas, "rect", (20, 20), (36, 36))
    assert canvas._overlay.handle_at(QPointF(35, 35), 1.0) == "br"
    assert canvas._overlay.handle_at(QPointF(29, 37), 1.0) == "b"


# --- seeing what's selected (issue #87) ------------------------------------------------
def black_canvas():
    image = QImage(240, 140, QImage.Format_RGB32)
    image.fill(QColor("#000000"))
    return make_canvas(image)


def brightest_near(canvas, scene_pts, spread=3) -> int:
    """The lightest pixel the viewport shows within `spread` px of any of these scene points."""
    shot = canvas.viewport().grab().toImage()
    best = 0
    for pt in scene_pts:
        view = canvas.mapFromScene(pt)
        for dx in range(-spread, spread + 1):
            for dy in range(-spread, spread + 1):
                x, y = view.x() + dx, view.y() + dy
                if shot.valid(x, y):
                    best = max(best, shot.pixelColor(x, y).lightness())
    return best


def test_selection_outline_shows_on_a_black_snip(qapp):
    canvas = black_canvas()
    a = drag(canvas, "rect", (30, 30), (100, 100))
    b = drag(canvas, "rect", (140, 30), (210, 100))
    canvas._select([a, b])  # several selected, as when picking things to delete: outlines, no handles
    rect = a.sceneBoundingRect()  # the outline goes round the hit area, clear of the red line
    edge = [QPointF(rect.left() + rect.width() * f, rect.top()) for f in (0.3, 0.5, 0.7)]
    assert brightest_near(canvas, edge, spread=2) > 200


def test_drag_to_select_rectangle_shows_on_a_black_snip(qapp):
    canvas = black_canvas()
    canvas.set_tool("select")
    send(canvas, "press", (30, 30))
    send(canvas, "move", (90, 70))
    send(canvas, "move", (150, 110))
    try:
        edge = [QPointF(x, 30) for x in (70, 90, 110)]
        assert brightest_near(canvas, edge, spread=2) > 200
    finally:
        send(canvas, "release", (150, 110))
    assert canvas._overlay._band is None  # gone once you let go


def test_blur_and_magnify_draw_over_markup(qapp, white_image):
    canvas = make_canvas(white_image)
    rect = drag(canvas, "rect", (10, 10), (190, 90))
    canvas.apply_style(fill=True)
    for tool in ("blur", "magnify"):
        canvas._scene.clearSelection()
        drag(canvas, tool, (60, 40), (100, 70))
    assert sorted(i.kind for i in canvas.annotations()) == ["blur", "magnify", "rect"]
    assert rect.pos() == QPointF(10, 10)


def test_arrow_draws_over_an_unselected_blur_but_moves_a_selected_one(qapp, white_image):
    canvas = make_canvas(white_image)
    blur = drag(canvas, "blur", (20, 20), (120, 80))
    canvas._scene.clearSelection()
    arrow = drag(canvas, "arrow", (60, 50), (160, 50))  # a blurred area looks like part of the snip
    assert arrow is not None and blur.pos() == QPointF(20, 20)
    canvas._select([blur])
    gesture(canvas, (40, 30), (50, 40))
    assert blur.pos() == QPointF(30, 30)


def test_arrow_draws_over_a_highlight(qapp, white_image):
    canvas = make_canvas(white_image)
    highlight = drag(canvas, "highlight", (20, 50), (180, 50))
    canvas._scene.clearSelection()
    arrow = drag(canvas, "arrow", (100, 50), (150, 90))  # highlight first, then point at it: a usual order
    assert arrow is not None and highlight.pos() == QPointF(0, 0)


def test_step_tool_drags_a_blur(qapp, white_image):
    canvas = make_canvas(white_image)
    blur = drag(canvas, "blur", (20, 20), (120, 80))
    canvas._scene.clearSelection()
    canvas.set_tool("step")
    gesture(canvas, (40, 30), (50, 40))
    assert blur.pos() == QPointF(30, 30)


def test_click_in_a_callouts_padding_keeps_typing(qapp, white_image):
    canvas = make_canvas(white_image)
    item = callout(canvas, (20, 10), (150, 90), text="Hello world")
    cursor = item.text.textCursor()
    cursor.setPosition(2)
    item.text.setTextCursor(cursor)
    steps = canvas.undo_stack.count()
    send(canvas, "press", (85, 22))  # above the text, inside the box
    send(canvas, "release", (85, 22))
    assert canvas.editing_text_item() is item.text
    assert item.text.textCursor().position() == 2
    assert canvas.undo_stack.count() == steps


def test_small_markup_moves_by_its_middle(qapp, white_image):
    canvas = make_canvas(white_image)
    rect = drag(canvas, "rect", (40, 40), (54, 54))
    canvas.apply_style(fill=True)
    gesture(canvas, (47, 47), (67, 57))
    assert rect.scene_rect() == QRectF(60, 50, 14, 14)


def test_a_selected_spotlight_outlines_just_the_lit_area(qapp, white_image):
    canvas = make_canvas(white_image)
    spot = drag(canvas, "spotlight", (40, 20), (120, 70))
    assert canvas.selected_annotations() == [spot]
    assert spot.selection_rects() == [QRectF(40, 20, 80, 50)]


def test_the_draw_over_hint_names_option_on_a_mac(qapp, on_a_mac):
    from snipforge.editor import panel as panel_module
    from snipforge.editor.style import DEFAULT_TOOL_STYLES
    panel = panel_module.PropertiesPanel()
    panel.load("arrow", DEFAULT_TOOL_STYLES["arrow"], False)
    assert f"hold {'Option' if sys.platform == 'darwin' else 'Alt'}" in panel._hint.text()  # this machine
    on_a_mac(panel_module)
    panel.load("arrow", DEFAULT_TOOL_STYLES["arrow"], False)
    assert "hold Option" in panel._hint.text()
