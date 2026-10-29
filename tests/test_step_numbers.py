"""Step numbers as a sequence: each numbering style counts on its own, you can start over from 1,
deleting steps closes the gap, and every step can be selected to restyle them all at once."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snipforge.editor.items import RectItem, StepItem
from snipforge.editor.panel import PropertiesPanel
from snipforge.editor.style import DEFAULT_TOOL_STYLES
from tests.test_editor import drag, gesture, make_canvas


@pytest.fixture
def canvas(white_image):
    c = make_canvas(white_image)
    c.set_tool("step")
    return c


def place(canvas, *xs):
    """Click the Step tool at each x (one row), returning the new steps in order."""
    placed = []
    for x in xs:
        gesture(canvas, (x, 30), (x, 30))
        placed.append(max((i for i in canvas.annotations() if isinstance(i, StepItem)), key=lambda i: i.zValue()))
    return placed


def labels(canvas):
    steps = sorted((i for i in canvas.annotations() if isinstance(i, StepItem)), key=lambda i: i.zValue())
    return [s.label() for s in steps]


# ---------------------------------------------------------------- each numbering counts on its own
def test_switching_to_letters_starts_at_a(canvas):
    place(canvas, 20, 50)
    QTest.keyClick(canvas, Qt.Key_Escape)  # the step just placed is selected: let it go
    assert canvas.tool == "step" and not canvas.selected_annotations()
    canvas.apply_style(numbering="letters")  # the Step tool's numbering
    assert canvas.next_step_number() == 1
    place(canvas, 80, 110)
    assert labels(canvas) == ["1", "2", "A", "B"]
    QTest.keyClick(canvas, Qt.Key_Escape)
    canvas.apply_style(numbering="digits")
    assert canvas.next_step_number() == 3  # back to numbers: they carry on


def test_switching_the_selected_step_to_letters_makes_it_a(canvas):
    one, two, three = place(canvas, 20, 50, 80)  # 3 is still selected
    canvas.apply_style(numbering="letters")
    assert labels(canvas) == ["1", "2", "A"] and canvas.next_step_number() == 2  # B next
    canvas.undo_stack.undo()
    assert three.label() == "3"


def test_switching_all_steps_numbers_them_from_the_start(canvas):
    steps = place(canvas, 20, 50, 80)
    canvas.apply_style(numbering="letters")  # 3 becomes A
    canvas.select_steps()
    canvas.apply_style(numbering="roman")
    assert [s.label() for s in steps] == ["I", "II", "III"]  # in their order, from I


# ---------------------------------------------------------------- start over
def test_start_over_from_one_and_the_new_run_carries_on(canvas):
    place(canvas, 20, 50, 80)
    canvas.set_next_step_number(1)
    assert canvas.next_step_number() == 1  # lower than what's on the snip: allowed now
    place(canvas, 110, 140)
    assert labels(canvas) == ["1", "2", "3", "1", "2"]
    assert canvas.next_step_number() == 3
    canvas.undo_stack.undo()
    assert canvas.next_step_number() == 2  # the undone step's number is next again
    canvas.undo_stack.undo()
    assert canvas.next_step_number() == 1  # as you set it
    canvas.undo_stack.redo()
    assert canvas.next_step_number() == 2


def test_placing_without_setting_a_number_follows_the_snip(canvas):
    place(canvas, 20, 50)
    assert canvas.next_step_number() == 3
    canvas.undo_stack.undo()
    assert canvas.next_step_number() == 2


# ---------------------------------------------------------------- deleting closes the gap
def test_deleting_steps_renumbers_the_ones_after(canvas):
    one, two, three, four = place(canvas, 20, 50, 80, 110)
    canvas.set_tool("select")
    canvas._select([two, three])
    canvas.delete_selection()
    assert (one.number, four.number) == (1, 2) and labels(canvas) == ["1", "2"]
    assert canvas.next_step_number() == 3
    canvas.undo_stack.undo()  # one undo brings both back, numbered as they were
    assert labels(canvas) == ["1", "2", "3", "4"] and four.number == 4
    canvas.undo_stack.redo()
    assert labels(canvas) == ["1", "2"]


def test_cutting_a_step_renumbers_too(canvas):
    one, two, three = place(canvas, 20, 50, 80)
    canvas.set_tool("select")
    canvas._select([one])
    assert canvas.cut_selection()
    assert (two.number, three.number) == (1, 2)


def test_gaps_close_within_each_numbering_only(canvas):
    one, two, three = place(canvas, 20, 50, 80)
    QTest.keyClick(canvas, Qt.Key_Escape)
    canvas.apply_style(numbering="letters")
    a, b = place(canvas, 110, 140)
    canvas.set_tool("select")
    canvas._select([two])
    canvas.delete_selection()
    assert (one.number, three.number) == (1, 2) and (a.label(), b.label()) == ("A", "B")


def test_deleting_other_markup_leaves_steps_alone(canvas):
    one, two = place(canvas, 20, 50)
    box = drag(canvas, "rect", (20, 60), (80, 90))
    canvas.set_tool("select")
    canvas._select([box])
    canvas.delete_selection()
    assert (one.number, two.number) == (1, 2)


# ---------------------------------------------------------------- restyle them all
def test_select_all_steps_then_restyle_them_together(canvas):
    steps = place(canvas, 20, 50, 80)
    box = drag(canvas, "rect", (20, 60), (80, 90))
    canvas.set_tool("step")
    canvas.select_steps()
    assert canvas.tool == "select" and set(canvas.selected_annotations()) == set(steps)
    canvas.apply_style(color="#34c759", badge="square", numbering="roman")
    assert all(s.style.color == "#34c759" and s.style.badge == "square" for s in steps)
    assert [s.label() for s in steps] == ["I", "II", "III"]
    assert isinstance(box, RectItem) and box.style.color != "#34c759"


# ---------------------------------------------------------------- the panel
def test_the_next_box_shows_the_label_for_the_numbering(qapp):
    panel = PropertiesPanel()
    letters = DEFAULT_TOOL_STYLES["step"].with_changes(numbering="letters")
    panel.load("step", letters, has_selection=False, next_step=3)
    assert panel.step_controls().next.text() == "C"
    panel.load("step", letters.with_changes(numbering="roman"), has_selection=False, next_step=4)
    assert panel.step_controls().next.text() == "IV"
    panel.load("step", DEFAULT_TOOL_STYLES["step"], has_selection=False, next_step=12)
    assert panel.step_controls().next.text() == "12"


def test_typing_a_letter_or_numeral_sets_the_next_number(qapp):
    panel = PropertiesPanel()
    panel.load("step", DEFAULT_TOOL_STYLES["step"].with_changes(numbering="letters"), has_selection=False,
               next_step=1)
    box = panel.step_controls().next
    assert box.valueFromText("AA") == 27 and box.valueFromText("c") == 3
    panel.load("step", DEFAULT_TOOL_STYLES["step"].with_changes(numbering="roman"), has_selection=False,
               next_step=1)
    assert box.valueFromText("xiv") == 14


def test_start_over_and_select_all_buttons(qapp):
    panel = PropertiesPanel()
    panel.load("step", DEFAULT_TOOL_STYLES["step"], has_selection=False, next_step=5)
    nexts, selects = [], []
    panel.next_step.connect(nexts.append)
    panel.select_steps.connect(lambda: selects.append(True))
    controls = panel.step_controls()
    controls.start_over.click()
    assert nexts == [1] and controls.next.value() == 1
    controls.select_all.click()
    assert selects == [True]


def test_select_all_steps_from_the_editors_panel(qapp, tmp_path, white_image):
    from snipforge.editor.window import EditorWindow
    from snipforge.library import Library
    lib = Library(tmp_path)
    editor = EditorWindow(white_image, lib, lib.create(white_image))
    editor.resize(1100, 700)
    editor.show()
    QApplication.processEvents()
    editor.canvas.set_tool("step")
    steps = place(editor.canvas, 20, 50)
    editor.panel.step_controls().select_all.click()
    assert set(editor.canvas.selected_annotations()) == set(steps)
    editor.close()
