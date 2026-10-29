"""Saved quick styles: save the selected markup's look for its tool, keep it across restarts, delete it."""
import json

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snipforge.editor import saved_styles
from snipforge.editor.panel import PropertiesPanel
from snipforge.editor.quick_styles import QUICK_STYLES
from snipforge.editor.style import DEFAULT_TOOL_STYLES, Style
from snipforge.settings import app_settings
from tests.test_editor import drag

MINE = Style(color="#123456", width=9.0, dash=True, shadow=False)


@pytest.fixture(autouse=True)
def _no_saved_styles():
    app_settings().remove(saved_styles.SETTING)
    yield
    app_settings().remove(saved_styles.SETTING)


def click(widget):
    QTest.mouseClick(widget, Qt.LeftButton, Qt.NoModifier, widget.rect().center())


# --- the store ---------------------------------------------------------------------------
def test_saved_styles_keep_what_the_tool_shows_and_survive_a_reload(qapp):
    assert saved_styles.save("arrow", MINE)
    assert not saved_styles.save("arrow", MINE)  # already there
    assert saved_styles.save("rect", MINE.with_changes(fill=True))
    loaded = saved_styles.load()
    assert saved_styles.fields_of("arrow", loaded["arrow"][0]) == {
        "color": "#123456", "width": 9.0, "dash": True, "dotted": False, "shadow": False,
        "heads": "end", "open_head": False, "curve": False, "opacity": 100, "rotation": 0}
    assert loaded["rect"][0].fill is True
    saved_styles.delete("arrow", MINE)
    assert "arrow" not in saved_styles.load() and saved_styles.saved_for("rect")


def test_each_tool_keeps_at_most_a_few(qapp):
    for n in range(saved_styles.MAX_SAVED + 2):
        saved_styles.save("line", Style(width=float(n + 1)))
    assert len(saved_styles.saved_for("line")) == saved_styles.MAX_SAVED
    assert not saved_styles.save("erase", Style())  # nothing to save for it


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", json.dumps({"arrow": "x", "laser": [{}], "rect": [5, {}]})])
def test_a_damaged_setting_reads_as_what_it_can(qapp, raw):
    app_settings().setValue(saved_styles.SETTING, raw)
    loaded = saved_styles.load()
    assert set(loaded) <= {"rect"}
    saved_styles.save("arrow", MINE)  # and saving still works
    assert saved_styles.saved_for("arrow")


# --- the panel -----------------------------------------------------------------------------
def test_save_the_selected_style_and_apply_it(qapp):
    panel = PropertiesPanel()
    panel.load("arrow", MINE, has_selection=True)
    assert panel.saved_count() == 0 and panel.save_button().isEnabled()
    click(panel.save_button())
    assert panel.saved_count() == 1 and not panel.save_button().isEnabled()  # it's saved already
    seen = []
    panel.changed.connect(seen.append)
    panel.load("arrow", DEFAULT_TOOL_STYLES["arrow"], has_selection=False)
    click(panel.saved_tile(0))
    assert seen == [{"color": "#123456", "width": 9.0, "dash": True, "dotted": False, "shadow": False,
                     "heads": "end", "open_head": False, "curve": False, "opacity": 100, "rotation": 0}]
    assert len([panel.quick_tile(i) for i in range(len(QUICK_STYLES))]) == 8  # built-ins stay


def test_saving_needs_a_selection(qapp):
    panel = PropertiesPanel()
    panel.load("arrow", DEFAULT_TOOL_STYLES["arrow"], has_selection=False)
    assert not panel.save_button().isEnabled() and "Select" in panel.save_button().toolTip()


def test_saved_styles_show_only_for_their_tool(qapp):
    saved_styles.save("arrow", MINE)
    panel = PropertiesPanel()
    panel.load("rect", DEFAULT_TOOL_STYLES["rect"], has_selection=False)
    assert panel.saved_count() == 0
    panel.load("arrow", DEFAULT_TOOL_STYLES["arrow"], has_selection=False)
    assert panel.saved_count() == 1


def test_tools_without_built_ins_can_still_save(qapp):
    panel = PropertiesPanel()
    panel.load("blur", Style(width=12.0), has_selection=True)
    assert not panel.section("quick").isHidden()
    assert all(panel.quick_tile(i).isHidden() for i in range(len(QUICK_STYLES)))
    click(panel.save_button())
    assert saved_styles.fields_of("blur", saved_styles.saved_for("blur")[0]) == {"width": 12.0, "rounded": False}
    panel.load("erase", DEFAULT_TOOL_STYLES["erase"], has_selection=True)
    assert panel.section("quick").isHidden()  # nothing to save for it


def test_delete_from_the_right_click_menu(qapp, monkeypatch):
    saved_styles.save("arrow", MINE)
    saved_styles.save("arrow", Style(color="#0a84ff"))
    panel = PropertiesPanel()
    panel.show()
    panel.load("arrow", DEFAULT_TOOL_STYLES["arrow"], has_selection=False)
    tile = panel.saved_tile(0)
    from snipforge.editor.quick_tiles import SavedStyleTile
    monkeypatch.setattr(SavedStyleTile, "_choose",
                        staticmethod(lambda menu, _pos: next(a for a in menu.actions() if "Delete" in a.text())))
    pos = tile.rect().center()
    QApplication.sendEvent(tile, QContextMenuEvent(QContextMenuEvent.Mouse, pos, tile.mapToGlobal(pos)))
    assert panel.saved_count() == 1
    assert saved_styles.saved_for("arrow")[0].color == "#0a84ff"


def test_delete_key_on_a_focused_tile(qapp):
    saved_styles.save("arrow", MINE)
    panel = PropertiesPanel()
    panel.load("arrow", DEFAULT_TOOL_STYLES["arrow"], has_selection=False)
    QTest.keyClick(panel.saved_tile(0), Qt.Key_Delete)
    assert panel.saved_count() == 0 and not saved_styles.saved_for("arrow")


def test_every_open_editor_sees_a_new_quick_style(qapp):
    first, second = PropertiesPanel(), PropertiesPanel()
    for panel in (first, second):
        panel.load("arrow", MINE, has_selection=True)
    click(first.save_button())
    assert second.saved_count() == 1


# --- in the editor ---------------------------------------------------------------------------
@pytest.fixture
def editor_factory(qapp, tmp_path, white_image):
    from snipforge.editor.window import EditorWindow
    from snipforge.library import Library
    lib = Library(tmp_path)

    def make():
        w = EditorWindow(white_image, lib, lib.create(white_image))
        w.resize(1100, 700)
        w.show()
        QApplication.processEvents()
        return w
    return make


def test_save_in_one_editor_use_it_after_a_restart(editor_factory):
    editor = editor_factory()
    canvas = editor.canvas
    arrow = drag(canvas, "arrow", (10, 10), (90, 50))
    canvas.apply_style(color="#123456", width=9.0, dash=True)
    click(editor.panel.save_button())
    assert "Saved" in editor.statusBar().currentMessage()
    canvas.apply_style(color="#ff3b30", width=4.0, dash=False)  # the arrow tool is back to normal
    editor.close()

    again = editor_factory()
    again.canvas.set_tool("arrow")
    click(again.panel.saved_tile(0))
    new = drag(again.canvas, "arrow", (10, 60), (90, 90))
    assert (new.style.color, new.style.width, new.style.dash) == ("#123456", 9.0, True)
    assert arrow.style.color == "#ff3b30"


def test_tiles_draw_their_style(qapp):
    saved_styles.save("rect", Style(color="#ff0000", fill=True, width=6.0))
    panel = PropertiesPanel()
    panel.show()
    panel.load("rect", DEFAULT_TOOL_STYLES["rect"], has_selection=False)
    image = panel.saved_tile(0).grab().toImage()
    reds = [image.pixelColor(x, y) for x in range(image.width()) for y in range(image.height())]
    assert any(c.red() > 200 and c.green() < 80 for c in reds)



# --- review: damaged settings, keyboard, names ---------------------------------------------
@pytest.mark.parametrize("entry", [
    {"width": "9"}, {"width": "abc"}, {"width": [1]}, {"width": float("nan")}, {"width": float("inf")},
    {"width": -1e15}, {"stamp": ["x"]}, {"font_size": 100000}, {"color": 7}, {"color": "not a color"},
    {"shadow": "yes"}, {"shape": "hexagon"},
])
def test_wrong_values_in_settings_never_reach_the_editor(qapp, entry):
    for kind in ("arrow", "pixelate", "stamp", "callout"):
        app_settings().setValue(saved_styles.SETTING, json.dumps({kind: [entry]}))
        style = saved_styles.saved_for(kind)[0]
        assert isinstance(style.width, float) and 0.5 <= style.width <= 200
        assert isinstance(style.stamp, str) and 4 <= style.font_size <= 400 and isinstance(style.shadow, bool)
        panel = PropertiesPanel()
        panel.load(kind, DEFAULT_TOOL_STYLES[kind], has_selection=False)
        panel.saved_tile(0).grab()  # paints
        seen = []
        panel.changed.connect(seen.append)
        click(panel.saved_tile(0))
        assert seen


def test_deeply_nested_settings_read_as_none(qapp):
    app_settings().setValue(saved_styles.SETTING, "[" * 200000)
    assert saved_styles.load() == {}
    app_settings().setValue("tool_styles", "[" * 200000)
    from snipforge.editor.window import load_tool_styles
    assert load_tool_styles(app_settings()) == {}
    app_settings().remove("tool_styles")


def test_styles_from_a_newer_version_are_kept_when_saving(qapp):
    app_settings().setValue(saved_styles.SETTING, json.dumps({"laser": [{"beam": 3}]}))
    saved_styles.save("arrow", MINE)
    stored = json.loads(app_settings().value(saved_styles.SETTING))
    assert stored["laser"] == [{"beam": 3}] and stored["arrow"]


def test_the_same_color_in_capitals_is_the_same_look(qapp):
    assert saved_styles.save("arrow", MINE)
    assert not saved_styles.save("arrow", MINE.with_changes(color="#123456".upper()))


def test_tiles_say_what_they_are(qapp):
    saved_styles.save("arrow", Style(color="#ff3b30", width=9.0, dash=True))
    saved_styles.save("arrow", Style(color="#0a84ff", width=3.0))
    panel = PropertiesPanel()
    panel.load("arrow", DEFAULT_TOOL_STYLES["arrow"], has_selection=False)
    names = [panel.saved_tile(i).accessibleName() for i in range(2)]
    assert names[0] != names[1] and "Red" in names[0] and "9 px" in names[0] and "dashed" in names[0]
    assert "Delete" in panel.saved_tile(0).toolTip()


def test_tiles_keep_focus_and_tab_in_place(qapp):
    saved_styles.save("arrow", MINE)
    saved_styles.save("arrow", Style(color="#0a84ff"))
    panel = PropertiesPanel()
    panel.show()
    panel.load("arrow", DEFAULT_TOOL_STYLES["arrow"], has_selection=False)
    first = panel.saved_tile(0)

    def next_stop(widget):  # where Tab goes: the next widget in the chain that takes Tab focus
        widget = widget.nextInFocusChain()
        while not widget.focusPolicy() & Qt.TabFocus:
            widget = widget.nextInFocusChain()
        return widget
    # Where they are on screen: the SAVED header's Remove all, then the saved tiles, then Save.
    assert next_stop(panel.quick_tile(len(QUICK_STYLES) - 1)) is panel.remove_all_button()
    assert next_stop(panel.remove_all_button()) is first
    assert next_stop(first) is panel.saved_tile(1) and next_stop(panel.saved_tile(1)) is panel.save_button()
    panel.load("arrow", MINE, has_selection=True)  # as after applying one: the same tiles stay
    assert panel.saved_tile(0) is first
    second = panel.saved_tile(1)
    second.setFocus()
    QTest.keyClick(second, Qt.Key_Delete)
    assert panel.saved_count() == 1
    assert QApplication.focusWidget() in (panel.saved_tile(0), None)  # None: no active window offscreen


def test_backspace_does_not_delete(qapp):
    saved_styles.save("arrow", MINE)
    panel = PropertiesPanel()
    panel.load("arrow", DEFAULT_TOOL_STYLES["arrow"], has_selection=False)
    QTest.keyClick(panel.saved_tile(0), Qt.Key_Backspace)
    assert panel.saved_count() == 1


def test_quick_styles_hide_when_theres_nothing_to_do(qapp):
    panel = PropertiesPanel()
    panel.load("blur", DEFAULT_TOOL_STYLES["blur"], has_selection=False)
    assert panel.section("quick").isHidden()
    saved_styles.save("blur", Style(width=9.0))
    panel.load("blur", DEFAULT_TOOL_STYLES["blur"], has_selection=False)
    assert not panel.section("quick").isHidden()


def test_samples_stay_visible_on_either_theme(qapp):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter

    from snipforge.editor.style_sample import paint_sample
    for background, color in (("#f6f6f8", "#ffffff"), ("#2b2b30", "#1c1c1e")):
        for kind in ("arrow", "rect", "pen", "text"):
            image = QImage(48, 40, QImage.Format_ARGB32)
            image.fill(QColor(background))
            p = QPainter(image)
            paint_sample(p, QRectF(4, 6, 40, 28), kind, Style(color=color, width=6.0), QColor(background))
            p.end()
            changed = sum(1 for x in range(48) for y in range(40) if image.pixelColor(x, y) != QColor(background))
            assert changed > 30, (kind, background)


# --- removing: the × on hover, and Remove all --------------------------------------------------
def enter(widget, pos=None):
    """The pointer is over it, as when it slides under a pointer that isn't moving."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QEnterEvent
    at = QPointF(pos if pos is not None else widget.rect().center())
    QApplication.sendEvent(widget, QEnterEvent(at, at, QPointF(widget.mapToGlobal(at.toPoint()))))


def move(widget, pos):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent
    at = QPointF(pos)
    QApplication.sendEvent(widget, QMouseEvent(QEvent.MouseMove, at, QPointF(widget.mapToGlobal(pos)),
                                               Qt.NoButton, Qt.NoButton, Qt.NoModifier))


def hover(widget, pos=None):
    """The pointer moves onto it."""
    pos = pos if pos is not None else widget.rect().center()
    enter(widget, pos)
    move(widget, pos)


def unhover(widget):
    from PySide6.QtCore import QEvent
    QApplication.sendEvent(widget, QEvent(QEvent.Leave))


def away_from_x(tile):
    """The bottom-left corner: on the tile, well clear of the × at its top right."""
    from PySide6.QtCore import QPoint
    spot = QPoint(6, tile.height() - 6)
    assert not tile.remove_rect().contains(spot)
    return spot


def arrow_panel():
    panel = PropertiesPanel()
    panel.show()
    panel.load("arrow", DEFAULT_TOOL_STYLES["arrow"], has_selection=False)
    QApplication.processEvents()
    return panel


def test_delete_all_keeps_other_tools(qapp):
    saved_styles.save("arrow", MINE)
    saved_styles.save("arrow", Style(color="#0a84ff"))
    saved_styles.save("rect", MINE)
    saved_styles.delete_all("arrow")
    assert not saved_styles.saved_for("arrow") and saved_styles.saved_for("rect")


def test_hovering_a_saved_style_shows_an_x_that_removes_just_that_one(qapp):
    saved_styles.save("arrow", MINE)
    saved_styles.save("arrow", Style(color="#0a84ff"))
    panel = arrow_panel()
    seen = []
    panel.changed.connect(seen.append)
    tile = panel.saved_tile(0)
    assert not tile.remove_shown()
    hover(tile)
    assert tile.remove_shown()
    click_at = tile.remove_rect().center()
    QTest.mouseClick(tile, Qt.LeftButton, Qt.NoModifier, click_at)
    assert panel.saved_count() == 1 and saved_styles.saved_for("arrow")[0].color == "#0a84ff"
    assert seen == []  # removing isn't applying


def test_the_x_hides_again_when_the_mouse_leaves(qapp):
    saved_styles.save("arrow", MINE)
    panel = arrow_panel()
    tile = panel.saved_tile(0)
    hover(tile)
    unhover(tile)
    assert not tile.remove_shown()


def test_a_focused_tile_shows_its_x_too(qapp):
    saved_styles.save("arrow", MINE)
    panel = arrow_panel()
    tile = panel.saved_tile(0)
    tile.setFocus(Qt.TabFocusReason)
    QApplication.processEvents()
    assert tile.remove_shown()


def test_clicking_the_tile_away_from_the_x_still_applies_it(qapp):
    saved_styles.save("arrow", MINE)
    panel = arrow_panel()
    seen = []
    panel.changed.connect(seen.append)
    tile = panel.saved_tile(0)
    hover(tile)
    QTest.mouseClick(tile, Qt.LeftButton, Qt.NoModifier, away_from_x(tile))
    assert panel.saved_count() == 1 and len(seen) == 1


def test_pressing_on_the_x_and_releasing_elsewhere_does_nothing(qapp):
    saved_styles.save("arrow", MINE)
    panel = arrow_panel()
    seen = []
    panel.changed.connect(seen.append)
    tile = panel.saved_tile(0)
    hover(tile)
    QTest.mousePress(tile, Qt.LeftButton, Qt.NoModifier, tile.remove_rect().center())
    QTest.mouseRelease(tile, Qt.LeftButton, Qt.NoModifier, away_from_x(tile))
    assert panel.saved_count() == 1 and seen == []


def test_remove_all_asks_first_then_clears_only_this_tool(qapp, monkeypatch):
    from snipforge.editor.quick_tiles import QuickStyles
    for color in ("#123456", "#0a84ff", "#34c759"):
        saved_styles.save("arrow", Style(color=color))
    saved_styles.save("rect", MINE)
    panel = arrow_panel()
    button = panel.remove_all_button()
    assert button.isVisible() and "3" in button.toolTip() and "arrows" in button.toolTip()
    asked = []
    monkeypatch.setattr(QuickStyles, "_confirm", lambda self, text: asked.append(text) or False)
    click(button)
    assert asked and "3" in asked[0] and panel.saved_count() == 3  # said no: all still there
    monkeypatch.setattr(QuickStyles, "_confirm", lambda self, text: True)
    click(button)
    assert panel.saved_count() == 0 and not saved_styles.saved_for("arrow") and saved_styles.saved_for("rect")
    assert not button.isVisible()  # nothing left to remove


def test_remove_all_is_hidden_without_saved_styles(qapp):
    panel = arrow_panel()
    assert not panel.remove_all_button().isVisible()


def test_remove_all_from_the_right_click_menu(qapp, monkeypatch):
    from snipforge.editor.quick_tiles import QuickStyles, SavedStyleTile
    saved_styles.save("arrow", MINE)
    saved_styles.save("arrow", Style(color="#0a84ff"))
    panel = arrow_panel()
    tile = panel.saved_tile(0)
    monkeypatch.setattr(QuickStyles, "_confirm", lambda self, text: True)
    monkeypatch.setattr(SavedStyleTile, "_choose",
                        staticmethod(lambda menu, _pos: next(a for a in menu.actions() if "all" in a.text())))
    pos = tile.rect().center()
    QApplication.sendEvent(tile, QContextMenuEvent(QContextMenuEvent.Mouse, pos, tile.mapToGlobal(pos)))
    assert panel.saved_count() == 0 and not saved_styles.saved_for("arrow")


# --- review: no double removals, focus after Remove all, a lost release -----------------------
def test_a_quick_second_click_does_not_remove_the_next_style(qapp):
    for color in ("#123456", "#0a84ff", "#34c759"):
        saved_styles.save("arrow", Style(color=color))
    panel = arrow_panel()
    tile = panel.saved_tile(0)
    hover(tile, tile.remove_rect().center())
    QTest.mouseClick(tile, Qt.LeftButton, Qt.NoModifier, tile.remove_rect().center())
    assert panel.saved_count() == 2
    under = panel.saved_tile(0)  # slid into the place the pointer is
    enter(under, under.remove_rect().center())  # Windows says the pointer is over it at once
    QTest.mouseClick(under, Qt.LeftButton, Qt.NoModifier, under.remove_rect().center())
    QTest.mouseDClick(under, Qt.LeftButton, Qt.NoModifier, under.remove_rect().center())
    assert panel.saved_count() == 2  # its × waits for the pointer to move first
    move(under, under.remove_rect().center() + QPoint(1, 0))
    QTest.mouseClick(under, Qt.LeftButton, Qt.NoModifier, under.remove_rect().center())
    assert panel.saved_count() == 1


def test_remove_all_from_the_keyboard_leaves_focus_on_something_you_can_use(qapp, monkeypatch):
    from snipforge.editor.quick_tiles import QuickStyles
    saved_styles.save("arrow", MINE)
    panel = arrow_panel()  # nothing selected: Save this style is off
    button = panel.remove_all_button()
    button.setFocus(Qt.TabFocusReason)
    QApplication.processEvents()

    def answer_yes(self, _text):
        QApplication.focusWidget() and QApplication.focusWidget().clearFocus()  # a dialog takes focus
        return True
    monkeypatch.setattr(QuickStyles, "_confirm", answer_yes)
    click(button)
    focused = QApplication.focusWidget()
    assert focused is not None and focused.isEnabled() and focused.isVisible()
    assert focused.accessibleName() or getattr(focused, "text", lambda: "")()


def test_a_press_on_the_x_whose_release_went_missing_does_not_eat_the_next_click(qapp):
    saved_styles.save("arrow", MINE)
    panel = arrow_panel()
    seen = []
    panel.changed.connect(seen.append)
    tile = panel.saved_tile(0)
    hover(tile)
    QTest.mousePress(tile, Qt.LeftButton, Qt.NoModifier, tile.remove_rect().center())  # no release arrives
    QTest.mouseClick(tile, Qt.LeftButton, Qt.NoModifier, away_from_x(tile))
    assert len(seen) == 1 and panel.saved_count() == 1
