"""Customize tools: the window that arranges the rail. Every change applies at once."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snipforge.editor import palette as palette_store
from snipforge.editor.customize_tools import DIVIDER, CustomizeToolsDialog
from snipforge.editor.palette import TOOL_NAMES, Palette
from snipforge.settings import app_settings


@pytest.fixture(autouse=True)
def clean(qapp):
    app_settings().remove(palette_store.SETTING)
    yield
    app_settings().remove(palette_store.SETTING)


@pytest.fixture
def dialog(qapp):
    d = CustomizeToolsDialog(Palette())
    d.show()
    QApplication.processEvents()
    yield d
    d.close()


def rows(dialog) -> list[str]:
    lst = dialog.list_widget()
    return [lst.item(i).data(Qt.UserRole) for i in range(lst.count())]


def settle():
    for _ in range(3):
        QApplication.processEvents()


def test_the_list_is_every_tool_with_a_divider_between_groups(dialog):
    names = rows(dialog)
    assert [n for n in names if n != DIVIDER] == TOOL_NAMES
    assert names.count(DIVIDER) == len(Palette().groups) - 1
    assert names[:2] == ["select", DIVIDER]


def test_unticking_hides_and_ticking_shows(dialog):
    lst = dialog.list_widget()
    item = next(lst.item(i) for i in range(lst.count()) if lst.item(i).data(Qt.UserRole) == "blur")
    item.setCheckState(Qt.Unchecked)
    settle()
    assert palette_store.load().hidden == {"blur"}
    item = next(lst.item(i) for i in range(lst.count()) if lst.item(i).data(Qt.UserRole) == "blur")
    item.setCheckState(Qt.Checked)
    settle()
    assert palette_store.load().hidden == set()


def test_move_up_and_down_reorder_the_rail(dialog):
    lst = dialog.list_widget()
    lst.setCurrentRow(rows(dialog).index("crop"))
    dialog._down.click()
    settle()
    assert palette_store.load().groups[-1] == ["cutout", "crop", "move"]
    assert dialog.list_widget().currentItem().data(Qt.UserRole) == "crop"
    dialog._up.click()
    dialog._up.click()  # over the divider: into the group above
    settle()
    assert palette_store.load().groups[-2][-1] == "crop" and palette_store.load().groups[-1] == ["cutout", "move"]


def test_alt_arrows_move_too(dialog):
    lst = dialog.list_widget()
    lst.setCurrentRow(rows(dialog).index("line"))
    QTest.keyClick(dialog, Qt.Key_Up, Qt.AltModifier)
    settle()
    assert palette_store.load().groups[1][:2] == ["line", "arrow"]


def test_favorite_button_stars_the_tool(dialog):
    lst = dialog.list_widget()
    lst.setCurrentRow(rows(dialog).index("text"))
    assert dialog._star.text() == "Favorite"
    dialog._star.click()
    settle()
    assert palette_store.load().favorites == ["text"]
    assert dialog.list_widget().currentItem().text().startswith("★")
    assert dialog._star.text() == "Unfavorite"
    dialog._star.click()
    settle()
    assert palette_store.load().favorites == []


def test_dividers_come_and_go(dialog):
    lst = dialog.list_widget()
    lst.setCurrentRow(rows(dialog).index("ellipse"))
    assert dialog._divider.text() == "Add divider" and dialog._divider.isEnabled()
    dialog._divider.click()
    settle()
    groups = palette_store.load().groups
    assert groups[1] == ["arrow", "line", "rect"] and groups[2][0] == "ellipse"
    lst = dialog.list_widget()
    assert lst.currentItem().data(Qt.UserRole) == "ellipse"
    lst.setCurrentRow(lst.currentRow() - 1)
    assert lst.currentItem().data(Qt.UserRole) == DIVIDER and dialog._divider.text() == "Remove divider"
    dialog._divider.click()
    settle()
    assert palette_store.load().groups == Palette().groups
    lst = dialog.list_widget()
    lst.setCurrentRow(rows(dialog).index("arrow"))  # right under a divider already: nothing to add
    assert not dialog._divider.isEnabled()


def test_switches_and_reset(dialog):
    dialog._labels.click()
    dialog._right.click()
    settle()
    p = palette_store.load()
    assert p.labels and p.side == "right"
    assert dialog._reset.isEnabled()
    dialog._reset.click()
    settle()
    assert palette_store.load().is_default()
    assert not dialog._labels.isChecked() and not dialog._right.isChecked() and not dialog._reset.isEnabled()


def test_a_change_elsewhere_shows_here_and_closing_stops_following(dialog):
    palette_store.save(Palette().toggled_hidden("pen"))
    settle()
    lst = dialog.list_widget()
    item = next(lst.item(i) for i in range(lst.count()) if lst.item(i).data(Qt.UserRole) == "pen")
    assert item.checkState() == Qt.Unchecked
    dialog.accept()
    palette_store.save(Palette())
    settle()  # nothing to fill any more, and no error
