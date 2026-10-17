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


@pytest.mark.parametrize("size", [(1024, 600), (1280, 640), (1100, 560), (1100, 700), (1440, 880)])
def test_every_tool_has_a_whole_visible_button(make_editor, size):
    editor = make_editor(*size)
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


def test_one_column_when_there_is_room_and_two_when_short(make_editor):
    tall = make_editor(1440, 880)
    assert tall.tool_rail.columns() == 1
    assert len({r.x() for r in _button_rects(tall).values()}) == 1
    short = make_editor(1024, 600)
    assert short.tool_rail.columns() == 2
    assert len({r.x() for r in _button_rects(short).values()}) == 2
    assert short.tool_rail.width() > tall.tool_rail.width()
    short.resize(1440, 880)  # making the window taller goes back to one column
    QApplication.processEvents()
    assert short.tool_rail.columns() == 1


def test_window_never_gets_too_short_to_show_every_tool(make_editor):
    editor = make_editor(1024, 300)
    assert editor.height() > 300
    rail = editor.tool_rail
    for name in TOOL_NAMES:
        button = rail.button(name)
        assert rail.rect().contains(QRect(button.pos(), button.size())), name


def test_tool_buttons_pick_the_tool_and_follow_the_canvas(make_editor):
    editor = make_editor(1024, 600)
    crop = editor.tool_rail.button("crop")
    QTest.mouseClick(crop, Qt.LeftButton, Qt.NoModifier, crop.rect().center())
    assert editor.canvas.tool == "crop" and crop.isChecked()
    editor.canvas.set_tool("spotlight")
    assert editor.tool_rail.button("spotlight").isChecked() and not crop.isChecked()
    assert editor.tool_rail.button("spotlight").toolTip() == "Spotlight (S)"
    assert editor.tool_rail.button("spotlight").accessibleName()
