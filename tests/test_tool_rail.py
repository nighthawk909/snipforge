"""The editor's tool rail: every tool always has a visible button, however short the window."""
import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton

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
    for name in dict.fromkeys(rail.shown()):
        for n, button in enumerate(rail.buttons_for(name)):
            rects[name if not n else f"{name} (favorite)"] = QRect(button.mapTo(editor, button.rect().topLeft()),
                                                                 button.size())
    return rects


def _assert_every_button_whole_and_apart(editor):
    rail_rect = QRect(editor.tool_rail.mapTo(editor, editor.tool_rail.rect().topLeft()), editor.tool_rail.size())
    rects = _button_rects(editor)
    assert set(rects) >= set(TOOL_NAMES) - set(editor.tool_rail.palette().hidden)
    for name, rect in rects.items():
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
    with_customize = rail.sizeHint().height()  # one column, and the Customize button under it
    _resize(editor, 1440, editor.height() - (rail.height() - with_customize))
    assert rail.height() == with_customize and rail.columns() == 1
    assert rail.customize_button().isVisible()
    _resize(editor, 1440, editor.height() - 1)
    assert rail.columns() == 1 and not rail.customize_button().isVisible()  # Customize steps aside first
    one_column = rail.height_for_columns(1)
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


# ---------------------------------------------------------------- your own palette (palette.py)
from snipforge.editor import palette as palette_store  # noqa: E402
from snipforge.editor.palette import Palette  # noqa: E402


@pytest.fixture(autouse=True)
def default_palette():
    from snipforge.settings import app_settings
    app_settings().remove(palette_store.SETTING)
    yield
    app_settings().remove(palette_store.SETTING)


def test_favorites_show_twice_hidden_tools_not_at_all(make_editor):
    editor = make_editor(1440, 1000)
    palette_store.save(Palette().toggled_favorite("crop").toggled_favorite("text").toggled_hidden("pixelate"))
    QApplication.processEvents()
    rail = editor.tool_rail
    assert rail.shown()[:2] == ["crop", "text"]  # Favorites, at the top, in the order you starred them
    assert rail.shown().count("crop") == 2 and "pixelate" not in rail.shown()
    assert len(rail.buttons_for("crop")) == 2
    _assert_every_button_whole_and_apart(editor)
    # Both of a favorite's buttons pick it, and both show it's on
    top, own = rail.buttons_for("crop")
    QTest.mouseClick(own, Qt.LeftButton, Qt.NoModifier, own.rect().center())
    assert editor.canvas.tool == "crop" and top.isChecked() and own.isChecked()
    # A hidden tool's key still works, and the rail copes with a tool it doesn't show
    editor.canvas.set_tool("pixelate")
    assert editor.canvas.tool == "pixelate" and not any(b.isChecked() for b in rail.buttons_for("crop"))


def test_labels_show_when_there_is_room_and_step_aside_when_not(make_editor):
    editor = make_editor(1440, 1000)
    rail = editor.tool_rail
    plain = rail.width()
    palette_store.save(Palette().with_changes(labels=True))
    QApplication.processEvents()
    assert rail.labelled() and rail.columns() == 1 and rail.width() > plain
    assert rail.button("arrow").text() == "Arrow" and rail.button("magnify").text() == "Magnify"
    assert rail.button("arrow").toolButtonStyle() == Qt.ToolButtonTextBesideIcon
    assert rail.customize_button().text() == "Customize"
    _resize(editor, 1440, 650)
    assert not rail.labelled() and rail.columns() == 2 and rail.width() > plain
    assert rail.button("arrow").toolButtonStyle() == Qt.ToolButtonIconOnly
    _assert_every_button_whole_and_apart(editor)
    _resize(editor, 1440, 1000)
    assert rail.labelled()


def test_the_rail_can_be_on_the_right(make_editor):
    editor = make_editor(1440, 900)
    rail, canvas = editor.tool_rail, editor.canvas

    def left(widget) -> int:  # in the window: the canvas sits inside the editor's stack of snips
        return widget.mapTo(editor, QPoint(0, 0)).x()
    assert left(rail) < left(canvas)
    palette_store.save(Palette().with_changes(side="right"))
    QApplication.processEvents()
    assert left(rail) > left(canvas) and rail.property("side") == "right"
    assert left(rail) + rail.width() <= editor.width()
    palette_store.save(Palette())
    QApplication.processEvents()
    assert left(rail) < left(canvas) and rail.property("side") == "left"


def test_the_right_click_menu_edits_the_palette(make_editor):
    editor = make_editor(1440, 900)
    rail = editor.tool_rail
    texts = lambda menu: [a.text() for a in menu.actions() if a.text()]  # noqa: E731
    menu = rail.menu_for("crop")
    assert texts(menu)[:2] == ["Add to favorites", "Hide Crop"]
    assert "Customize tools…" in texts(menu) and "Show labels" in texts(menu)
    next(a for a in menu.actions() if a.text() == "Add to favorites").trigger()
    QApplication.processEvents()
    assert palette_store.load().favorites == ["crop"] and rail.shown()[0] == "crop"
    menu = rail.menu_for("crop")
    assert texts(menu)[0] == "Remove from favorites"
    next(a for a in menu.actions() if a.text() == "Hide Crop").trigger()
    QApplication.processEvents()
    assert "crop" not in rail.shown()
    next(a for a in rail.menu_for(None).actions() if a.text() == "Tools on the right").trigger()
    QApplication.processEvents()
    assert palette_store.load().side == "right"
    reset = next(a for a in rail.menu_for(None).actions() if a.text().startswith("Reset"))
    assert reset.isEnabled()
    reset.trigger()
    QApplication.processEvents()
    assert palette_store.load().is_default() and rail.shown() == TOOL_NAMES
    assert not next(a for a in rail.menu_for(None).actions() if a.text().startswith("Reset")).isEnabled()


def test_every_open_editor_follows_the_palette(make_editor):
    first, second = make_editor(1440, 900), make_editor(1440, 900)
    palette_store.save(Palette().toggled_hidden("line"))
    QApplication.processEvents()
    assert "line" not in first.tool_rail.shown() and "line" not in second.tool_rail.shown()
    first.close()
    palette_store.save(Palette())  # a closed editor no longer listens (and doesn't crash)
    QApplication.processEvents()
    assert "line" in second.tool_rail.shown()


def test_keyboard_reaches_the_customize_button_too(make_editor):
    editor = make_editor(1440, 1000)
    editor.activateWindow()
    rail = editor.tool_rail
    last = rail.button(TOOL_NAMES[-1])
    rail._set_stop(TOOL_NAMES[-1])
    last.setFocus(Qt.TabFocusReason)
    QTest.keyClick(last, Qt.Key_Down)
    assert QApplication.focusWidget() is rail.customize_button()
    QTest.keyClick(rail.customize_button(), Qt.Key_Up)
    assert QApplication.focusWidget() is last


def test_the_rail_keeps_one_tab_stop_however_often_the_palette_changes(make_editor):
    editor = make_editor(1440, 1000)
    rail = editor.tool_rail
    action = editor._tool_actions["crop"]
    before = action.receivers("2toggled(bool)")
    for _ in range(5):
        palette_store.save(Palette().toggled_favorite("text"))
        palette_store.save(Palette())
        QApplication.processEvents()
    assert action.receivers("2toggled(bool)") == before  # connected once, not once per layout
    palette_store.save(Palette().toggled_hidden("crop"))
    QApplication.processEvents()
    editor.canvas.set_tool("crop")  # its key still works...
    stops = [b for b in rail.findChildren(QToolButton) if b.focusPolicy() & Qt.TabFocus]
    assert len(stops) == 1  # ...and Tab still reaches the rail


def test_with_every_tool_hidden_the_customize_button_is_the_tab_stop(make_editor):
    editor = make_editor(1440, 1000)
    hidden = Palette()
    for name in TOOL_NAMES:
        hidden = hidden.toggled_hidden(name)
    palette_store.save(hidden)
    QApplication.processEvents()
    rail = editor.tool_rail
    assert rail.shown() == []
    assert rail.customize_button().focusPolicy() & Qt.TabFocus and rail.customize_button().isVisible()
    assert rail.customize_button().y() < editor.tool_rail.height()


def test_the_rail_is_laid_out_once_at_startup(make_editor, monkeypatch):
    from snipforge.editor.tool_rail import ToolRail

    calls = []
    real = ToolRail.set_palette
    monkeypatch.setattr(ToolRail, "set_palette", lambda self, p: calls.append(p) or real(self, p))
    make_editor(1440, 1000)
    assert len(calls) == 1
