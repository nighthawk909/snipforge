"""Ctrl+K: find a tool or action by name and run it."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from snipforge.editor import palette as palette_store
from snipforge.editor.command_palette import Command, CommandPalette, ranked, score
from snipforge.editor.palette import TOOL_NAMES, Palette
from snipforge.editor.window import EditorWindow
from snipforge.library import Library
from snipforge.settings import app_settings


@pytest.fixture(autouse=True)
def clean():
    app_settings().remove(palette_store.SETTING)
    yield
    app_settings().remove(palette_store.SETTING)


@pytest.fixture
def editor(qapp, tmp_path, white_image):
    lib = Library(tmp_path)
    w = EditorWindow(white_image, lib, lib.create(white_image), on_new_snip=lambda: None)
    w.resize(1200, 800)
    w.show()
    QApplication.processEvents()
    yield w
    w.close()


def test_matching_is_by_word_starts_best_first():
    arrow = Command("Arrow", lambda: None, "tool", "A", "arrow")
    rotate = Command("Rotate right", lambda: None, key="Ctrl+R", keywords=("turn", "image"))
    save_as = Command("Save as…", lambda: None)
    assert score(arrow, "arr") > score(arrow, "row") > 0 and score(arrow, "xyz") == 0
    assert score(rotate, "turn") > 0 and score(rotate, "right") > 0 and score(rotate, "rot ri") > 0
    assert score(save_as, "save as") > score(save_as, "as")
    assert ranked([rotate, arrow, save_as], "a")[0] is arrow  # a label start beats a word inside
    assert ranked([rotate, arrow, save_as], "ri") == [rotate]
    assert ranked([rotate, arrow, save_as], "") == [rotate, arrow, save_as]  # nothing typed: all, in order


def test_every_tool_action_and_quick_style_is_findable(editor):
    commands = editor.commands_for_palette()
    labels = [c.label for c in commands]
    assert "Arrow" in labels and "Smart Move" in labels and "Customize tools…" in labels
    assert "Red outline" not in labels  # nothing selected and Select picked: no style to change
    editor.canvas.set_tool("arrow")
    assert "Red outline" in [c.label for c in editor.commands_for_palette()]
    assert all(c.key for c in commands if c.kind == "tool")
    assert len([c for c in commands if c.kind == "tool"]) == len(TOOL_NAMES)
    palette_store.save(Palette().toggled_hidden("blur"))
    QApplication.processEvents()
    hidden = next(c for c in editor.commands_for_palette() if c.label == "Blur")
    assert "hidden" in hidden.keywords  # a hidden tool is still here, and "hidden" finds it


def test_typing_filters_and_enter_runs(editor):
    editor.find_command()
    palette = editor._palette_window
    assert palette.isVisible() and palette.field().hasFocus()
    QTest.keyClicks(palette.field(), "cro")
    assert palette.current().label == "Crop"
    QTest.keyClick(palette.field(), Qt.Key_Return)
    QApplication.processEvents()
    assert editor.canvas.tool == "crop" and not (isValid(palette) and palette.isVisible())  # closed, and let go
    assert editor.focusWidget() is editor.canvas  # the keyboard goes back to the snip


def test_arrows_move_and_escape_closes_without_running(editor):
    editor.find_command()
    palette = editor._palette_window
    first = palette.current()
    QTest.keyClick(palette.field(), Qt.Key_Down)
    assert palette.current() is not first
    QTest.keyClick(palette.field(), Qt.Key_Up)
    assert palette.current() is first
    QTest.keyClick(palette.field(), Qt.Key_Escape)
    QApplication.processEvents()
    assert palette.ran() is None and editor.canvas.tool == "select"


def test_nothing_matching_says_so(editor):
    editor.find_command()
    palette = editor._palette_window
    QTest.keyClicks(palette.field(), "zzqx")
    assert palette.shown() == [] and palette.current() is None
    QTest.keyClick(palette.field(), Qt.Key_Return)  # nothing to run: stays open
    assert palette.isVisible()
    palette.reject()


def test_the_keyboard_shortcut_and_the_toolbar_button_open_it(editor):
    editor.activateWindow()
    editor.canvas.setFocus()
    QTest.keyClick(editor.canvas, Qt.Key_K, Qt.ControlModifier)
    QApplication.processEvents()
    assert editor._palette_window.isVisible()
    editor._palette_window.reject()
    QApplication.processEvents()
    editor.commands.find_button.click()
    assert editor._palette_window.isVisible()
    assert "K" in editor.commands.find_button.toolTip()
    editor._palette_window.reject()


def test_actions_run_what_they_say(editor):
    rotate = next(c for c in editor.commands_for_palette() if c.label == "Rotate right")
    before = editor.canvas.crop_rect().size()
    rotate.run()
    after = editor.canvas.crop_rect().size()
    assert (after.width(), after.height()) == (before.height(), before.width())
    labels = next(c for c in editor.commands_for_palette() if c.label == "Show tool labels")
    labels.run()
    QApplication.processEvents()
    assert palette_store.load().labels
    assert any(c.label == "Hide tool labels" for c in editor.commands_for_palette())


def test_a_quick_style_from_the_palette_changes_the_tool(editor):
    editor.canvas.set_tool("rect")
    blue = next(c for c in editor.commands_for_palette() if c.label == "Blue")
    assert blue.kind == "style" and blue.color == "#0a84ff"
    blue.run()
    assert editor.canvas.tool_styles["rect"].color == "#0a84ff" and editor.canvas.tool_styles["rect"].fill


def test_the_palette_window_standalone(qapp):
    seen = []
    p = CommandPalette([Command("One", lambda: seen.append(1)), Command("Two", lambda: seen.append(2))])
    p.show()
    QTest.keyClicks(p.field(), "tw")
    QTest.keyClick(p.field(), Qt.Key_Return)
    assert seen == [2] and p.ran().label == "Two"
