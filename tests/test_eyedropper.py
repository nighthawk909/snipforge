"""The eyedropper: pick any pixel's color from the snip into the current color, with a loupe."""
import pytest
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snipforge.editor import colors
from tests.test_editor import drag, make_canvas, send


@pytest.fixture
def striped(qapp):
    """200x100: left half orange (#ff9500), right half a teal (#2cb1bc)."""
    img = QImage(200, 100, QImage.Format_ARGB32)
    img.fill(QColor("#2cb1bc"))
    for x in range(100):
        for y in range(100):
            img.setPixelColor(x, y, QColor("#ff9500"))
    return img


def hover(canvas, scene_pt):
    """A mouse move with no button held, as when you move the pointer around."""
    view_pt = QPointF(canvas.mapFromScene(QPointF(*scene_pt)))
    QApplication.sendEvent(canvas.viewport(), QMouseEvent(
        QEvent.MouseMove, view_pt, canvas.viewport().mapToGlobal(view_pt), Qt.NoButton, Qt.NoButton,
        Qt.NoModifier))


def click(canvas, scene_pt):
    send(canvas, "press", scene_pt)
    send(canvas, "release", scene_pt)


def test_picking_sets_the_tools_color(striped):
    c = make_canvas(striped)
    c.set_tool("arrow")
    c.start_picking()
    assert c.picking()
    click(c, (150, 50))
    assert c.tool_styles["arrow"].color == "#2cb1bc"
    assert not c.picking() and c.annotations() == []  # the click picked; it didn't draw


def test_picking_recolors_the_selection_undoably(striped):
    c = make_canvas(striped)
    rect = drag(c, "rect", (10, 10), (60, 60))
    c.start_picking()
    click(c, (150, 50))
    assert rect.style.color == "#2cb1bc" and rect.isSelected()
    c.undo_stack.undo()
    assert rect.style.color == "#ff3b30"


def test_picks_what_you_see_markup_included(striped):
    c = make_canvas(striped)
    c.set_tool("pen")
    c.apply_style(color="#bf5af2", width=10.0)
    drag(c, "pen", (120, 20), (190, 20))  # a thick purple line over the teal
    c.set_tool("arrow")
    c.start_picking()
    click(c, (150, 20))
    assert c.tool_styles["arrow"].color == "#bf5af2"


def test_loupe_shows_the_pixels_under_the_pointer(striped):
    c = make_canvas(striped)
    c.set_tool("arrow")
    c.start_picking()
    hover(c, (98, 50))
    loupe = c._loupe
    assert loupe.isVisible() and loupe.label() == "#FF9500"
    grab = loupe.grab().toImage()
    left, right = grab.pixelColor(20, 20).name(), grab.pixelColor(grab.width() - 20, 20).name()
    assert left == "#ff9500" and right == "#2cb1bc"  # both sides of the edge, enlarged
    hover(c, (101, 50))
    assert loupe.label() == "#2CB1BC"


def test_escape_cancels_without_changing_anything(striped):
    c = make_canvas(striped)
    c.set_tool("arrow")
    c.start_picking()
    hover(c, (150, 50))
    QApplication.sendEvent(c, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert not c.picking() and not c._loupe.isVisible()
    assert c.tool == "arrow" and c.tool_styles["arrow"].color == "#ff3b30"


def test_double_clicking_a_callout_with_the_eyedropper_only_picks(striped):
    from tests.test_editor import dbl
    c = make_canvas(striped)
    callout = drag(c, "callout", (110, 20), (190, 80))
    callout.text.setPlainText("Note")
    c.commit_text()
    c.set_tool("arrow")
    for _ in range(2):  # each double-click picks once and starts no text editing
        c.start_picking()
        send(c, "press", (150, 50))
        send(c, "release", (150, 50))
        dbl(c, (150, 50))
        send(c, "release", (150, 50))
        assert c.editing_text_item() is None and not c.picking()


def test_choosing_a_tool_ends_picking(striped):
    c = make_canvas(striped)
    c.start_picking()
    c.set_tool("rect")
    assert not c.picking()


def test_clicks_outside_the_snip_keep_picking(striped):
    c = make_canvas(striped)
    c.set_tool("arrow")
    c.set_crop_rect(QRectF(0, 0, 100, 100))
    c.start_picking()
    click(c, (150, 50))  # cropped away: hidden, so not pickable
    click(c, (230, 50))  # past the image
    assert c.picking() and c.tool_styles["arrow"].color == "#ff3b30"
    click(c, (50, 50))
    assert c.tool_styles["arrow"].color == "#ff9500"


def test_picking_follows_a_turned_snip(striped):
    c = make_canvas(striped)
    c.rotate_image(True)
    c.set_tool("arrow")
    c.start_picking()
    click(c, (50, 20))  # turned clockwise, the top of the image was its left half
    assert c.tool_styles["arrow"].color == "#ff9500"
    c.start_picking()
    click(c, (50, 180))
    assert c.tool_styles["arrow"].color == "#2cb1bc"


# --- the panel and the editor window ---------------------------------------------------------
@pytest.fixture
def editor(qapp, tmp_path, striped):
    from snipforge.editor.window import EditorWindow
    from snipforge.library import Library
    lib = Library(tmp_path)
    w = EditorWindow(striped, lib, lib.create(striped))
    w.resize(1100, 700)
    w.show()
    QApplication.processEvents()
    yield w
    w.close()


def test_eyedropper_button_picks_into_the_color_row(editor):
    canvas, panel = editor.canvas, editor.panel
    canvas.set_tool("rect")
    QTest.mouseClick(panel.pick_button(), Qt.LeftButton)
    assert canvas.picking() and panel.pick_button().isChecked()
    click(canvas, (150, 50))
    assert canvas.tool_styles["rect"].color == "#2cb1bc"
    assert not panel.pick_button().isChecked()
    assert not any(s.isChecked() for s in panel._swatches)  # a custom color, so no swatch ring
    assert colors.recent()[0] == "#2cb1bc"  # and it's first in Recent
    assert "#2CB1BC" in editor.statusBar().currentMessage()


def test_clicking_the_eyedropper_again_cancels(editor):
    editor.canvas.set_tool("rect")
    QTest.mouseClick(editor.panel.pick_button(), Qt.LeftButton)
    QTest.mouseClick(editor.panel.pick_button(), Qt.LeftButton)
    assert not editor.canvas.picking() and not editor.panel.pick_button().isChecked()


def test_eyedropper_sits_beside_the_custom_color_and_fits(editor):
    from snipforge.editor.panel import PANEL_WIDTH
    for kind in ("rect", "text", "callout", "arrow"):
        editor.canvas.set_tool(kind)
        assert editor.panel.pick_button().isVisibleTo(editor)
        assert editor.panel.widget().minimumSizeHint().width() <= PANEL_WIDTH - 14
    editor.canvas.set_tool("blur")  # no color to pick into
    assert not editor.panel.pick_button().isVisibleTo(editor)


# --- review: picking always ends before the snip changes, and other keys wait ----------------
def test_any_change_to_the_snip_ends_picking(striped):
    c = make_canvas(striped)
    drag(c, "rect", (10, 10), (60, 60))
    c.set_tool("arrow")
    for change in (c.undo_stack.undo, c.undo_stack.redo, lambda: c.rotate_image(True)):
        c.start_picking()
        change()
        assert not c.picking()
    c.start_picking()
    click(c, (50, 150))  # inside the turned image, outside the old picture
    assert not c.picking() and c.tool_styles["arrow"].color in ("#ff9500", "#2cb1bc")


def test_other_keys_wait_while_picking(striped):
    c = make_canvas(striped)
    rect = drag(c, "rect", (10, 10), (60, 60))
    c.start_picking()
    for key in (Qt.Key_Delete, Qt.Key_Return, Qt.Key_Right):
        QApplication.sendEvent(c, QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))
    assert c.picking() and rect in c.annotations() and rect.pos() == QPointF(10, 10)
    QApplication.sendEvent(c, QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.NoModifier))  # a tool key
    assert not c.picking() and c.tool == "arrow"


def test_right_click_cancels(striped):
    c = make_canvas(striped)
    c.set_tool("arrow")
    c.start_picking()
    view_pt = QPointF(c.mapFromScene(QPointF(150, 50)))
    QApplication.sendEvent(c.viewport(), QMouseEvent(QEvent.MouseButtonPress, view_pt, view_pt, Qt.RightButton,
                                                     Qt.RightButton, Qt.NoModifier))
    assert not c.picking() and c.tool_styles["arrow"].color == "#ff3b30"


def test_space_drag_still_pans_while_picking(striped):
    big = striped.scaled(800, 400)
    c = make_canvas(big)
    c.set_zoom(4.0)
    c.start_picking()
    QApplication.sendEvent(c, QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier))
    before = c.horizontalScrollBar().value()
    start = QPointF(300, 200)
    QApplication.sendEvent(c.viewport(), QMouseEvent(QEvent.MouseButtonPress, start, start, Qt.LeftButton,
                                                     Qt.LeftButton, Qt.NoModifier))
    for dx in (20, 60, 120):
        at = start + QPointF(dx, 0)
        QApplication.sendEvent(c.viewport(), QMouseEvent(QEvent.MouseMove, at, at, Qt.NoButton, Qt.LeftButton,
                                                         Qt.NoModifier))
    assert c.horizontalScrollBar().value() != before and c.picking()


def test_a_mid_pixel_pointer_picks_the_pixel_under_it(striped):
    c = make_canvas(striped)
    c.set_tool("arrow")
    c.start_picking()
    view = c.mapFromScene(QPointF(99, 50))  # the last orange column...
    at = QPointF(view) + QPointF(0.6, 0.2)  # ...with the pointer in its right half, as at 150% scaling
    for kind, buttons in ((QEvent.MouseButtonPress, Qt.LeftButton), (QEvent.MouseButtonRelease, Qt.NoButton)):
        QApplication.sendEvent(c.viewport(), QMouseEvent(kind, at, at, Qt.LeftButton, buttons, Qt.NoModifier))
    assert c.tool_styles["arrow"].color == "#ff9500"


def test_losing_focus_ends_picking(striped):
    from PySide6.QtGui import QFocusEvent
    c = make_canvas(striped)
    c.start_picking()
    c.focusOutEvent(QFocusEvent(QEvent.FocusOut, Qt.ActiveWindowFocusReason))
    assert not c.picking()


def test_cancelling_clears_the_hint(editor):
    editor.canvas.set_tool("rect")
    QTest.mouseClick(editor.panel.pick_button(), Qt.LeftButton)
    assert "pick its color" in editor.statusBar().currentMessage()
    QApplication.sendEvent(editor.canvas, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert editor.statusBar().currentMessage() == ""
