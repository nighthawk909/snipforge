"""The editor's tool rail: every tool always has a visible button, however short the window."""
import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snipforge.editor.window import TOOLS, EditorWindow
from snipforge.library import Library
from snipforge.theme.styles import build_stylesheet
from snipforge.theme.tokens import BUILTIN_ACCENTS, DEFAULT_ACCENT, resolve

TOOL_NAMES = [spec[0] for spec in TOOLS if spec is not None]


@pytest.fixture(params=["light", "dark"])
def make_editor(request, qapp, tmp_path, white_image):
    """With the app's real stylesheet, so buttons are the size people see."""
    qapp.setStyleSheet(build_stylesheet(resolve(request.param, BUILTIN_ACCENTS[DEFAULT_ACCENT])))
    made = []

    def build(width: int, height: int) -> EditorWindow:
        lib = Library(tmp_path)
        w = EditorWindow(white_image, lib, lib.create(white_image), on_new_snip=lambda: None)
        made.append(w)
        w.resize(width, height)
        w.show()
        QApplication.processEvents()
        return w

    yield build
    for w in made:
        w.close()
    qapp.setStyleSheet("")


def _button_rects(editor) -> dict[str, QRect]:
    rail = editor.tool_rail
    rects = {}
    for name in TOOL_NAMES:
        button = rail.button(name)
        rects[name] = QRect(button.mapTo(editor, button.rect().topLeft()), button.size())
    return rects


def _assert_every_button_whole_and_apart(editor):
    rail_rect = QRect(editor.tool_rail.mapTo(editor, editor.tool_rail.rect().topLeft()), editor.tool_rail.size())
    rects = _button_rects(editor)
    for name, rect in rects.items():
        assert editor.tool_rail.button(name).isVisible(), name
        assert rail_rect.contains(rect), (name, rect, rail_rect)
        assert editor.rect().contains(rect), (name, rect, editor.size())
        assert rect.width() >= 32 and rect.height() >= 32, (name, rect)  # still an easy target
    names = list(rects)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert not rects[a].intersects(rects[b]), (a, b)


@pytest.mark.parametrize("size", [(1024, 600), (1280, 640), (1100, 560), (1100, 700), (1024, 470), (1440, 880)])
def test_every_tool_has_a_whole_visible_button(make_editor, size):
    editor = make_editor(*size)
    assert editor.height() == size[1]
    _assert_every_button_whole_and_apart(editor)


def _resize(editor, width: int, height: int) -> None:
    editor.resize(width, height)
    QApplication.processEvents()


def test_the_fewest_columns_that_fit(make_editor):
    editor = make_editor(1440, 1000)
    rail = editor.tool_rail
    widths = {}
    for height, columns in ((1000, 1), (650, 2), (470, 3), (650, 2), (1000, 1)):  # shrinking, then growing
        _resize(editor, 1440, height)
        assert rail.columns() == columns, height
        assert len({r.x() for r in _button_rects(editor).values()}) == columns
        assert rail.width() == rail.sizeHint().width()
        widths[columns] = rail.width()
        _assert_every_button_whole_and_apart(editor)
    assert widths[1] < widths[2] < widths[3]


def test_one_column_from_exactly_its_height(make_editor):
    editor = make_editor(1440, 1000)
    rail = editor.tool_rail
    one_column = rail.sizeHint().height()
    _resize(editor, 1440, editor.height() - (rail.height() - one_column))
    assert rail.height() == one_column and rail.columns() == 1
    _resize(editor, 1440, editor.height() - 1)
    assert rail.columns() == 2


def test_window_never_gets_too_short_to_show_every_tool(make_editor):
    editor = make_editor(1024, 300)
    assert editor.height() >= editor.minimumSizeHint().height() > 300
    assert editor.minimumSizeHint().height() <= 470  # fits a 1080p screen at 200% scaling
    assert editor.tool_rail.columns() == 3
    _assert_every_button_whole_and_apart(editor)


def test_a_style_with_bigger_buttons_reflows_the_rail(make_editor, qapp):
    editor = make_editor(1024, 600)
    qapp.setStyleSheet(qapp.styleSheet() + "#ToolRail QToolButton { padding: 12px; }")
    for _ in range(3):
        QApplication.processEvents()
    assert editor.tool_rail.button("crop").width() >= 46
    _assert_every_button_whole_and_apart(editor)


def test_tool_buttons_pick_the_tool_and_follow_the_canvas(make_editor):
    editor = make_editor(1024, 600)
    crop = editor.tool_rail.button("crop")
    QTest.mouseClick(crop, Qt.LeftButton, Qt.NoModifier, crop.rect().center())
    assert editor.canvas.tool == "crop" and crop.isChecked()
    editor.canvas.set_tool("spotlight")
    assert editor.tool_rail.button("spotlight").isChecked() and not crop.isChecked()
    assert editor.tool_rail.button("spotlight").toolTip() == "Spotlight (S)"
    assert editor.tool_rail.button("spotlight").accessibleName() == "Spotlight (S)"
    assert editor.tool_rail.accessibleName() == "Tools"


def test_clicking_a_tool_leaves_the_keyboard_on_the_canvas(make_editor):
    editor = make_editor(1024, 600)
    editor.activateWindow()
    editor.canvas.setFocus()
    blur = editor.tool_rail.button("blur")
    QTest.mouseClick(blur, Qt.LeftButton, Qt.NoModifier, blur.rect().center())
    QApplication.processEvents()
    assert editor.canvas.tool == "blur"
    assert QApplication.focusWidget() is editor.canvas
