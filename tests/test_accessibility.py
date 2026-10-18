"""Accessibility: every control has a name a screen reader can say, everything works from the
keyboard (and you can see where the keyboard is), and SnipForge follows the system's high-contrast
setting (Windows' contrast themes, the Mac's Increase contrast: snipforge/theme/contrast.py)."""
from __future__ import annotations

import dataclasses

import pytest
from PySide6.QtCore import QEvent, QRect, QRectF, Qt
from PySide6.QtGui import QAccessible, QColor, QContextMenuEvent, QGuiApplication, QImage, QKeyEvent, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLineEdit,
    QScrollBar,
    QTabBar,
    QTextEdit,
    QWidget,
)

from snipforge.editor.window import TOOLS, EditorWindow
from snipforge.library import Library
from snipforge.theme import contrast, tokens

INTERACTIVE = (QAbstractButton, QAbstractItemView, QAbstractSlider, QAbstractSpinBox, QComboBox, QLineEdit,
               QTabBar, QTextEdit)
HC_BLACK = contrast.SystemColors(window="#000000", text="#FFFFFF", highlight="#1AEBFF", highlight_text="#000000",
                                 button="#000000", button_text="#FFFFFF", disabled="#3FF23F", link="#FFFF00")
HC_WHITE = contrast.SystemColors(window="#FFFFFF", text="#000000", highlight="#37006E", highlight_text="#FFFFFF",
                                 button="#FFFFFF", button_text="#000000", disabled="#600000", link="#00009F")


def unnamed(window: QWidget) -> list[str]:
    """The controls in `window` a screen reader would announce without a name."""
    missing = []
    for w in window.findChildren(QWidget):
        if not w.isVisibleTo(window) or isinstance(w, QScrollBar):  # Qt's own scroll bars say what they are
            continue
        ours = type(w).__module__.startswith("snipforge") and w.focusPolicy() != Qt.NoFocus
        if not isinstance(w, INTERACTIVE) and not ours:
            continue
        iface = QAccessible.queryAccessibleInterface(w)
        if iface is None or not iface.text(QAccessible.Name).strip():
            missing.append(f"{type(w).__name__} {w.objectName()!r} in {type(w.parentWidget()).__name__}")
    return missing


def active(window: QWidget) -> None:
    window.show()
    window.activateWindow()
    QTest.qWaitForWindowActive(window)


@pytest.fixture
def snip():
    image = QImage(640, 400, QImage.Format_ARGB32)
    image.fill(QColor("#ffffff"))
    return image


@pytest.fixture
def editor(qapp, snip, tmp_path):
    library = Library(tmp_path)
    window = EditorWindow(snip, library, library.create(snip))
    window.resize(1100, 700)
    active(window)
    yield window
    window.close()


# --- Names a screen reader can say -------------------------------------------------------------
def test_every_control_in_the_editor_has_a_name(editor):
    assert unnamed(editor) == []
    for tool in [t[0] for t in TOOLS if t]:
        editor.canvas.set_tool(tool)
        assert unnamed(editor.panel) == [], tool
    assert "snip" in editor.canvas.accessibleName().lower() and "Tab" in editor.canvas.accessibleDescription()


def test_every_control_in_the_library_and_settings_has_a_name(qapp, tmp_path, monkeypatch, snip):
    from snipforge import autostart
    from snipforge.home import HomeWindow
    from snipforge.settings_window import SECTIONS, SettingsWindow
    from tests.test_settings_window import FakeController

    library = Library(tmp_path / "lib")
    library.create(snip)
    home = HomeWindow(library, lambda: None, lambda _i: None)
    home.resize(1100, 700)
    home.show()
    assert unnamed(home) == []
    home.close()
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    settings = SettingsWindow(library_root=tmp_path, update_controller=FakeController())
    settings.resize(1100, 800)
    settings.show()
    for section in SECTIONS:
        settings.show_section(section)
        assert unnamed(settings) == [], section
    settings.close()


def test_every_control_while_taking_a_snip_has_a_name(qapp):
    from snipforge.capture import _ScreenOverlay

    desk = QPixmap(1000, 700)
    desk.fill(QColor("#1E3550"))
    overlay = _ScreenOverlay(QGuiApplication.primaryScreen(), desk, [QRect(100, 80, 300, 200)], mode="area",
                             toolbar=True, scrolling=True)
    overlay.resize(1000, 700)
    overlay.show()
    assert unnamed(overlay) == [] and overlay.accessibleName()
    overlay.close()


# --- The editor from the keyboard --------------------------------------------------------------
def test_the_tool_rail_is_one_tab_stop_with_the_arrow_keys_inside_it(editor):
    rail = editor.tool_rail
    stops = [b for b in rail.findChildren(QAbstractButton) if b.focusPolicy() & Qt.TabFocus]
    assert stops == [rail.button("select")]  # one stop: the tool you're on
    rail.button("select").setFocus()
    QTest.keyClick(QApplication.focusWidget(), Qt.Key_Down)
    assert QApplication.focusWidget() is rail.button("arrow")
    QTest.keyClick(QApplication.focusWidget(), Qt.Key_End)
    QTest.keyClick(QApplication.focusWidget(), Qt.Key_Up)
    picked = QApplication.focusWidget()
    QTest.keyClick(picked, Qt.Key_Return)  # picks it and goes to the canvas, where it's used
    assert editor.canvas.tool == next(name for name in rail._buttons if rail.button(name) is picked)
    assert QApplication.focusWidget() is editor.canvas
    stops = [b for b in rail.findChildren(QAbstractButton) if b.focusPolicy() & Qt.TabFocus]
    assert stops == [picked]


def test_clicking_a_tool_leaves_the_keyboard_on_the_canvas(editor):
    editor.canvas.setFocus()
    QTest.mouseClick(editor.tool_rail.button("rect"), Qt.LeftButton)
    assert editor.canvas.tool == "rect" and QApplication.focusWidget() is editor.canvas


def test_f6_moves_between_the_parts_of_the_editor(editor):
    editor.canvas.setFocus()
    seen = []
    for _ in range(4):
        QTest.keyClick(QApplication.focusWidget(), Qt.Key_F6)
        seen.append(editor.region_of(QApplication.focusWidget()))
    assert seen == ["panel", "commands", "tools", "canvas"]
    QTest.keyClick(QApplication.focusWidget(), Qt.Key_F6, Qt.ShiftModifier)
    assert editor.region_of(QApplication.focusWidget()) == "tools"


def test_tab_in_the_canvas_walks_through_the_markup_and_then_moves_on(editor):
    canvas = editor.canvas
    canvas.set_tool("rect")
    canvas.setFocus()
    QTest.keyClick(canvas, Qt.Key_Return)  # a rectangle in the middle of what you can see
    canvas.set_tool("ellipse")
    QTest.keyClick(canvas, Qt.Key_Return)
    first, second = canvas.bottom_up(canvas.annotations())
    canvas.set_tool("select")
    canvas._scene.clearSelection()
    QTest.keyClick(canvas, Qt.Key_Tab)
    assert canvas.selected_annotations() == [first] and canvas.hasFocus()
    QTest.keyClick(canvas, Qt.Key_Tab)
    assert canvas.selected_annotations() == [second]
    QTest.keyClick(canvas, Qt.Key_Tab, Qt.ShiftModifier)
    assert canvas.selected_annotations() == [first]
    QTest.keyClick(canvas, Qt.Key_Tab)
    QTest.keyClick(canvas, Qt.Key_Tab)  # past the last: on to the panel, nothing left selected
    assert not canvas.hasFocus() and canvas.selected_annotations() == []


def test_enter_with_a_drawing_tool_puts_its_markup_in_the_middle(editor):
    canvas = editor.canvas
    canvas.setFocus()
    visible = canvas.mapToScene(canvas.viewport().rect()).boundingRect().intersected(canvas.crop_rect())
    kinds = {"rect": "rect", "ellipse": "ellipse", "arrow": "arrow", "line": "line", "blur": "blur",
             "pixelate": "pixelate", "erase": "erase", "spotlight": "spotlight", "magnify": "magnify",
             "step": "step", "stamp": "stamp", "callout": "callout"}
    for tool, kind in kinds.items():
        canvas.set_tool(tool)
        canvas.setFocus()
        QTest.keyClick(canvas, Qt.Key_Return)
        canvas.commit_text()  # a callout starts you typing
        added = canvas.selected_annotations()
        assert len(added) == 1 and added[0].kind == kind, tool
        assert visible.intersects(added[0].sceneBoundingRect()), tool
    canvas.set_tool("text")
    QTest.keyClick(canvas, Qt.Key_Return)
    assert canvas.editing_text_item() is not None  # type straight away
    QTest.keyClicks(canvas, "Hi")
    QTest.keyClick(canvas, Qt.Key_Escape)
    assert any(getattr(i, "toPlainText", lambda: "")() == "Hi" for i in canvas.annotations())
    said = []
    canvas.notice.connect(said.append)
    for tool in ("pen", "highlight", "move", "cutout"):  # these follow the pointer: nothing, and it says so
        canvas.set_tool(tool)
        before = len(canvas.annotations())
        QTest.keyClick(canvas, Qt.Key_Return)
        assert len(canvas.annotations()) == before and said and "pointer" in said[-1], tool
        said.clear()


def test_holding_enter_adds_one(editor):
    canvas = editor.canvas
    canvas.set_tool("step")
    canvas.setFocus()
    QTest.keyClick(canvas, Qt.Key_Return)
    for _ in range(4):  # the key held down: auto-repeats
        QApplication.sendEvent(canvas, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier, "", True))
    assert len(canvas.annotations()) == 1


def test_ctrl_arrows_resize_what_is_selected(editor):
    canvas = editor.canvas
    canvas.set_tool("rect")
    canvas.setFocus()
    QTest.keyClick(canvas, Qt.Key_Return)
    box = canvas.selected_annotations()[0]
    size = box.scene_rect().size()
    QTest.keyClick(canvas, Qt.Key_Right, Qt.ControlModifier)
    QTest.keyClick(canvas, Qt.Key_Down, Qt.ControlModifier | Qt.ShiftModifier)
    assert box.scene_rect().size().toTuple() == (size.width() + 1, size.height() + 10)
    QTest.keyClick(canvas, Qt.Key_Left, Qt.ControlModifier | Qt.ShiftModifier)
    assert box.scene_rect().width() == size.width() - 9
    canvas.undo_stack.undo()
    assert box.scene_rect().size() == size  # presses in a row are one step, as nudges are
    canvas.set_tool("stamp")
    canvas.setFocus()
    QTest.keyClick(canvas, Qt.Key_Return)
    stamp = canvas.selected_annotations()[0]
    QTest.keyClick(canvas, Qt.Key_Right, Qt.ControlModifier)
    QTest.keyClick(canvas, Qt.Key_Up, Qt.ControlModifier | Qt.ShiftModifier)
    w, h = stamp.scene_rect().size().toTuple()
    assert w == h  # stamps stay square


def test_the_crop_moves_and_sizes_with_the_keys(editor):
    canvas = editor.canvas
    canvas.set_tool("crop")
    canvas.setFocus()
    whole = canvas.crop_rect()
    QTest.keyClick(canvas, Qt.Key_Left, Qt.ControlModifier | Qt.ShiftModifier)  # 10 narrower
    QTest.keyClick(canvas, Qt.Key_Up, Qt.ControlModifier)  # 1 shorter
    QTest.keyClick(canvas, Qt.Key_Right)  # then moved a pixel right
    QTest.keyClick(canvas, Qt.Key_Right, Qt.ShiftModifier)  # and as far as it can go (9 more)
    QTest.keyClick(canvas, Qt.Key_Tab)  # Tab doesn't wander into the markup while cropping
    QTest.keyClick(canvas, Qt.Key_Return)  # keeps it
    assert canvas.crop_rect() == QRectF(whole.left() + 10, whole.top(), whole.width() - 10, whole.height() - 1)


# --- The Library and capture from the keyboard -------------------------------------------------
def test_the_menu_key_opens_the_menu_for_the_current_snip_where_it_is(qapp, tmp_path, snip, monkeypatch):
    from snipforge.home import HomeWindow

    library = Library(tmp_path)
    for _ in range(3):
        library.create(snip)
    home = HomeWindow(library, lambda: None, lambda _i: None)
    home.resize(1100, 700)
    home.show()
    QApplication.processEvents()
    shown = []
    monkeypatch.setattr(HomeWindow, "_show_menu", staticmethod(lambda menu, at: shown.append((menu, at))))
    grid = home.grid
    grid.setCurrentIndex(grid.model().index(2, 0))
    middle = grid.viewport().rect().center()
    QApplication.sendEvent(grid, QContextMenuEvent(QContextMenuEvent.Keyboard, middle,  # where Qt sends it
                                                   grid.viewport().mapToGlobal(middle)))
    menu, at = shown[0]
    assert grid.visualRect(grid.currentIndex()).contains(grid.viewport().mapFromGlobal(at))
    assert grid.selected_ids() == [grid.current_id()] and menu.actions()[0].text() == "Open"
    home.close()


def test_the_arrow_keys_pick_a_window_to_snip_without_the_mouse(qapp):
    from snipforge.capture import _ScreenOverlay

    desk = QPixmap(1000, 700)
    desk.fill(QColor("#1E3550"))
    windows = [QRect(100, 80, 300, 200), QRect(500, 300, 200, 150)]
    overlay = _ScreenOverlay(QGuiApplication.primaryScreen(), desk, windows, mode="window")
    overlay.resize(1000, 700)
    got = []
    overlay.selected.connect(got.append)
    QTest.keyClick(overlay, Qt.Key_Right)
    QTest.keyClick(overlay, Qt.Key_Right)
    QTest.keyClick(overlay, Qt.Key_Right)  # round to the first again
    QTest.keyClick(overlay, Qt.Key_Left)
    QTest.keyClick(overlay, Qt.Key_Return)
    assert [image.size().toTuple() for image in got] == [(200, 150)]
    assert overlay.accessibleName()


def test_a_window_chosen_with_the_keys_is_forgotten_when_the_mode_changes(qapp):
    from snipforge.capture import _ScreenOverlay

    desk = QPixmap(1000, 700)
    desk.fill(QColor("#1E3550"))
    overlay = _ScreenOverlay(QGuiApplication.primaryScreen(), desk, [QRect(100, 80, 300, 200)], mode="area")
    overlay.resize(1000, 700)
    got = []
    overlay.selected.connect(got.append)
    QTest.keyClick(overlay, Qt.Key_Left)  # Area mode: round to the whole monitor, its last choice
    QTest.keyClick(overlay, Qt.Key_W, Qt.NoModifier)
    QTest.keyClick(overlay, Qt.Key_Return)  # nothing chosen in Window mode: the whole monitor, no error
    QTest.keyClick(overlay, Qt.Key_Right)
    QTest.keyClick(overlay, Qt.Key_A, Qt.NoModifier)
    QTest.keyClick(overlay, Qt.Key_W, Qt.NoModifier)
    QTest.keyClick(overlay, Qt.Key_Return)  # the window isn't shown any more: not snipped
    assert [image.size().toTuple() for image in got] == [overlay.size().toTuple()] * 2


# --- High contrast ----------------------------------------------------------------------------
@pytest.mark.parametrize("colors", [HC_BLACK, HC_WHITE])
def test_a_contrast_theme_uses_the_system_colors(colors):
    theme = tokens.contrast_theme(colors)
    p, a, h = theme.palette, theme.accent, theme.header
    assert (p.bg, p.text, a.fill, a.on) == (colors.window, colors.text, colors.highlight, colors.highlight_text)
    assert theme.dark == (colors.window == "#000000")
    for fore, back in ((p.text, p.bg), (p.text2, p.surface), (p.text3, p.bg), (p.text, p.hover), (p.text, p.select),
                       (a.text, a.soft), (a.on, a.fill), (h.text, h.bg), (h.ink, h.bar), (p.border, p.bg)):
        assert tokens.contrast_ratio(fore, back) >= 7, (fore, back)  # WCAG AAA


def test_the_app_follows_the_system_contrast_setting(qapp, monkeypatch):
    from snipforge.theme.manager import ThemeManager

    monkeypatch.setattr(contrast, "high", lambda: False)
    manager = ThemeManager(qapp, packs=[])
    try:
        normal = manager.theme
        monkeypatch.setattr(contrast, "high", lambda: True)
        monkeypatch.setattr(contrast, "system_colors", lambda dark: HC_BLACK)
        manager.contrast_changed()
        assert manager.theme.palette.bg == "#000000" and manager.high_contrast
        assert "#1AEBFF" in qapp.styleSheet()  # focus and selection drawn in the system's highlight
        monkeypatch.setattr(contrast, "system_colors", lambda dark: HC_WHITE)  # another contrast theme
        QApplication.sendEvent(qapp.topLevelWidgets()[0] if qapp.topLevelWidgets() else qapp,
                               QEvent(QEvent.ThemeChange))
        QTest.qWait(20)
        assert manager.theme.palette.bg == "#FFFFFF"
        monkeypatch.setattr(contrast, "high", lambda: False)
        QGuiApplication.styleHints().accessibility().contrastPreferenceChanged.emit(Qt.ContrastPreference.NoPreference)
        assert manager.theme == normal
    finally:
        manager.close()
        qapp.setStyleSheet("")


def test_unreadable_system_colors_fall_back_to_black_and_white(monkeypatch):
    import sys
    import types

    import snipforge.theme

    gray = dataclasses.replace(HC_BLACK, window="#808080", text="#808080")
    fake = types.SimpleNamespace(colors=lambda: gray)  # Windows' colors, as _contrast_win32 reads them
    monkeypatch.setattr(contrast.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "snipforge.theme._contrast_win32", fake)
    monkeypatch.setattr(snipforge.theme, "_contrast_win32", fake, raising=False)
    assert contrast.system_colors(dark=True) == contrast.DARK
    fake.colors = lambda: HC_WHITE
    assert contrast.system_colors(dark=True) == HC_WHITE


def test_settings_say_when_the_system_contrast_setting_is_in_charge(qapp, tmp_path, monkeypatch):
    from snipforge import autostart
    from snipforge.settings_window import SettingsWindow
    from snipforge.theme.manager import ThemeManager
    from tests.test_settings_window import FakeController

    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    monkeypatch.setattr(contrast, "high", lambda: True)
    monkeypatch.setattr(contrast, "system_colors", lambda dark: HC_WHITE)
    manager = ThemeManager(qapp, packs=[])
    try:
        window = SettingsWindow(library_root=tmp_path, update_controller=FakeController(), theme_manager=manager)
        window.show_section("appearance")
        assert window.contrast_note.isVisibleTo(window) and "contrast" in window.contrast_note.text()
        assert not any(tile.isEnabled() for tile in window.theme_tiles.values())
        window.close()
    finally:
        manager.close()
        qapp.setStyleSheet("")


def focus_shows(host: QWidget, widget: QWidget, elsewhere: QWidget) -> bool:
    """Whether `widget` looks different with the keyboard on it (in its window, which is active)."""
    elsewhere.setFocus(Qt.TabFocusReason)
    QApplication.processEvents()
    plain = widget.grab().toImage()
    widget.setFocus(Qt.TabFocusReason)
    QApplication.processEvents()
    assert widget.hasFocus()
    return widget.grab().toImage() != plain


@pytest.mark.parametrize("theme_of", [lambda: tokens.resolve("light", tokens.BUILTIN_ACCENTS["snipforge"]),
                                      lambda: tokens.resolve("dark", tokens.BUILTIN_ACCENTS["teal"]),
                                      lambda: tokens.contrast_theme(HC_BLACK), lambda: tokens.contrast_theme(HC_WHITE)],
                         ids=["light", "dark", "contrast-black", "contrast-white"])
def test_where_the_keyboard_is_can_always_be_seen(qapp, theme_of):
    """Every kind of control, including the ones already drawn in the accent color (a primary
    button, a chosen option, a checked tool in high contrast, the record button)."""
    from PySide6.QtWidgets import QCheckBox, QPushButton, QToolButton, QVBoxLayout

    from snipforge.theme import styles
    from snipforge.widgets import ToggleSwitch

    def inside(name: str, child: QWidget) -> QWidget:
        box = QWidget()
        box.setObjectName(name)
        QVBoxLayout(box).addWidget(child)
        return box

    def button(cls, text="Go", name="", checked=None, variant=None):
        b = cls()
        b.setText(text)
        b.setObjectName(name)
        if checked is not None:
            b.setCheckable(True)
            b.setChecked(checked)
        if variant:
            b.setProperty("variant", variant)
        return b

    controls = {
        "button": button(QPushButton), "primary": button(QPushButton, variant="primary"),
        "bar primary": button(QPushButton, variant="primary"), "tool": button(QToolButton),
        "checked tool": button(QToolButton, checked=True), "rail tool": button(QToolButton, checked=True),
        "segment": button(QToolButton, checked=True), "accent choice": button(QPushButton, name="AccentChoice",
                                                                              checked=True),
        "record": button(QPushButton, name="RecordStart"), "check box": QCheckBox("Box"), "switch": ToggleSwitch("On"),
    }
    controls["check box"].setFocusPolicy(Qt.StrongFocus)
    for control in controls.values():
        control.setFocusPolicy(control.focusPolicy() | Qt.TabFocus)
    host = QWidget()
    column = QVBoxLayout(host)
    elsewhere = QLineEdit()
    column.addWidget(elsewhere)
    parents = {"bar primary": "CommandBar", "rail tool": "ToolRail", "segment": "Segmented"}
    for name, control in controls.items():
        column.addWidget(inside(parents[name], control) if name in parents else control)
    qapp.setStyleSheet(styles.build_stylesheet(theme_of(), {}))
    try:
        active(host)
        hidden = [name for name, control in controls.items() if not focus_shows(host, control, elsewhere)]
        assert hidden == []
    finally:
        host.close()
        qapp.setStyleSheet("")


def test_the_editor_has_no_light_dark_switch_while_the_system_contrast_is_on(editor, qapp, monkeypatch):
    from snipforge import theme
    from snipforge.theme.manager import ThemeManager

    monkeypatch.setattr(contrast, "high", lambda: True)
    monkeypatch.setattr(contrast, "system_colors", lambda dark: HC_BLACK)
    manager = ThemeManager(qapp, packs=[])
    try:
        monkeypatch.setattr(theme, "get_manager", lambda: manager)
        editor._retheme()
        assert not editor.commands.theme_button.isVisibleTo(editor)
    finally:
        manager.close()
        qapp.setStyleSheet("")

