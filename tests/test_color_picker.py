"""Pick a color from anywhere on screen: every monitor freezes, a loupe follows the pointer with the
exact screen pixel under it, and a click (or Enter) copies that pixel's color as HEX, RGB or HSL and
adds it to the editor's recent colors."""
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QEnterEvent, QGuiApplication, QKeyEvent, QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication

from snipforge import color_text, loupe, prefs, shortcuts
from snipforge.editor import colors
from snipforge.settings import app_settings
from snipforge.shortcuts import FIRST_ID, MOD_CONTROL, MOD_SHIFT
from tests.test_capture_fixed import coded_screen
from tests.test_shortcuts import FakeApi

LOGICAL = (640, 400)  # the monitor in these tests (in Windows' scaled pixels)
_SCREENS: dict[tuple[int, int], object] = {}


class FakeCursor:
    """Where the picker thinks the pointer is (the real one is never read or moved): in physical
    pixels for the keyboard's start, and in Qt's for which monitor gets the keyboard."""
    at = QPoint(-30000, -30000)  # off every monitor, unless a test puts it somewhere

    @classmethod
    def pos(cls, *_args) -> QPoint:
        return cls.at


@pytest.fixture(autouse=True)
def fake_cursor(monkeypatch):
    from snipforge import color_picker
    FakeCursor.at = QPoint(-30000, -30000)
    monkeypatch.setattr(color_picker, "QCursor", FakeCursor)
    monkeypatch.setattr(color_picker, "_pointer", FakeCursor.pos)


@pytest.fixture(autouse=True)
def clean_prefs():
    keys = [prefs.KEYS["color_format"], prefs.KEYS["shortcut_color"], colors.RECENT_SETTING]
    for k in keys:
        app_settings().remove(k)
    yield
    for k in keys:
        app_settings().remove(k)


def screen_image(width: int, height: int):
    if (width, height) not in _SCREENS:
        _SCREENS[(width, height)] = coded_screen(width, height)
    return _SCREENS[(width, height)]


def frozen_pixmap(dpr: float) -> QPixmap:
    pix = QPixmap.fromImage(screen_image(round(LOGICAL[0] * dpr), round(LOGICAL[1] * dpr)))
    pix.setDevicePixelRatio(dpr)
    return pix


def at(color: QColor) -> tuple[int, int]:
    """The screen pixel a coded screen's color came from (tests/test_capture_fixed.py)."""
    red, green, blue = color.red(), color.green(), color.blue()
    return (blue >> 4) << 8 | red, (blue & 15) << 8 | green


def coded(x: int, y: int) -> QColor:
    return QColor(x & 255, y & 255, (x >> 8) << 4 | y >> 8)


def make_overlay(dpr=1.0, copy_as="hex"):
    from snipforge.color_picker import PickOverlay
    o = PickOverlay(QGuiApplication.primaryScreen(), frozen_pixmap(dpr), copy_as=copy_as)
    o.resize(*LOGICAL)
    o.got, o.gave_up = [], []
    o.picked.connect(o.got.append)
    o.cancelled.connect(lambda: o.gave_up.append(True))
    return o


def mouse(widget, etype, pos, button=Qt.LeftButton, buttons=Qt.LeftButton):
    pt = QPointF(*pos)
    QApplication.sendEvent(widget, QMouseEvent(etype, pt, widget.mapToGlobal(pt), button, buttons, Qt.NoModifier))


def move(widget, pos):
    mouse(widget, QEvent.MouseMove, pos, Qt.NoButton, Qt.NoButton)


def click(widget, pos, button=Qt.LeftButton):
    move(widget, pos)
    mouse(widget, QEvent.MouseButtonPress, pos, button, button)
    mouse(widget, QEvent.MouseButtonRelease, pos, button, Qt.NoButton)


def key(widget, k, modifiers=Qt.NoModifier, text=""):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyPress, k, modifiers, text))


# --- writing a color down ------------------------------------------------------------------------

def test_a_color_is_written_as_hex_rgb_or_hsl():
    blue = QColor("#2f5bea")
    assert color_text.as_text(blue, "hex") == "#2F5BEA"
    assert color_text.as_text(blue, "rgb") == "rgb(47, 91, 234)"
    assert color_text.as_text(blue, "hsl") == "hsl(226, 82%, 55%)"
    assert color_text.summary(blue) == "HEX #2F5BEA · RGB 47, 91, 234"
    assert color_text.summary(blue, copied="hex") == "RGB 47, 91, 234"  # not twice in a notice
    assert color_text.summary(blue, copied="rgb") == "HEX #2F5BEA"


@pytest.mark.parametrize("hex_, hsl", [
    ("#000000", (0, 0, 0)), ("#ffffff", (0, 0, 100)), ("#808080", (0, 0, 50)), ("#ff0000", (0, 100, 50)),
    ("#00ff00", (120, 100, 50)), ("#0000ff", (240, 100, 50)),
    ("#ff0002", (0, 100, 50)),  # 359.5 degrees rounds to 360, which is red again: 0
])
def test_hsl_is_in_whole_degrees_and_percents(hex_, hsl):
    assert color_text.hsl(QColor(hex_)) == hsl


def test_the_formats_go_round_in_order_and_unknown_ones_are_refused():
    assert color_text.FORMATS == prefs.COLOR_FORMATS == ("hex", "rgb", "hsl")
    assert [color_text.next_format(f) for f in color_text.FORMATS] == ["rgb", "hsl", "hex"]
    assert color_text.next_format("hex", -1) == "hsl"
    with pytest.raises(ValueError):
        color_text.as_text(QColor("red"), "cmyk")


def test_the_copy_format_is_remembered_and_validated():
    assert prefs.color_format() == "hex"
    prefs.set_color_format("hsl")
    assert prefs.color_format() == "hsl"
    app_settings().setValue(prefs.KEYS["color_format"], "cmyk")
    assert prefs.color_format() == "hex"
    with pytest.raises(ValueError):
        prefs.set_color_format("cmyk")


# --- the picker on one monitor -------------------------------------------------------------------

def test_the_loupe_follows_the_pointer_with_the_hex_of_the_pixel_under_it(qapp):
    o = make_overlay()
    assert o.point() is None and o.loupe_rect() is None
    move(o, (120, 80))
    assert o.point() == QPoint(120, 80)
    assert o.label() == coded(120, 80).name().upper()
    box = o.loupe_rect()
    assert box is not None and not box.contains(QPoint(120, 80))  # beside the pointer, not under it
    assert abs(box.left() - 120) < 40 and abs(box.top() - 80) < 40


@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 1.75, 2.0])
def test_a_click_picks_the_exact_screen_pixel_at_any_display_scale(qapp, dpr):
    """Windows reports the pointer in whole screen pixels and Qt divides by the scale, so at 125% the
    pixel at 301 arrives as 240.8: the picker must get 301 back, not 300 or 302."""
    o = make_overlay(dpr)
    target = (301, 177)
    click(o, (target[0] / dpr, target[1] / dpr))
    assert len(o.got) == 1 and at(o.got[0]) == target


def test_arrow_keys_move_one_screen_pixel_and_enter_picks(qapp):
    o = make_overlay(1.5)
    move(o, (100, 100))
    assert o.point() == QPoint(150, 150)
    key(o, Qt.Key_Right)  # 151: between two of Windows' scaled pixels, out of the mouse's reach
    assert o.point() == QPoint(151, 150) and o.label() == coded(151, 150).name().upper()
    key(o, Qt.Key_Down)
    key(o, Qt.Key_Down)
    key(o, Qt.Key_Left, Qt.ShiftModifier)  # Shift: 10 at a time
    assert o.point() == QPoint(141, 152)
    key(o, Qt.Key_Return)
    assert [at(c) for c in o.got] == [(141, 152)]


def test_space_picks_too_and_a_held_enter_picks_once(qapp):
    o = make_overlay()
    move(o, (30, 40))
    QApplication.sendEvent(o, QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier, " "))
    QApplication.sendEvent(o, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier, "", True))  # auto-repeat
    assert [at(c) for c in o.got] == [(30, 40)]


def test_arrow_keys_stop_at_the_edges_of_the_monitor(qapp):
    o = make_overlay(1.25)
    move(o, (2, 2))
    for _ in range(3):
        key(o, Qt.Key_Left, Qt.ShiftModifier)
        key(o, Qt.Key_Up, Qt.ShiftModifier)
    assert o.point() == QPoint(0, 0)
    move(o, (LOGICAL[0] - 1, LOGICAL[1] - 1))
    for _ in range(3):
        key(o, Qt.Key_Right, Qt.ShiftModifier)
        key(o, Qt.Key_Down, Qt.ShiftModifier)
    assert o.point() == QPoint(round(LOGICAL[0] * 1.25) - 1, round(LOGICAL[1] * 1.25) - 1)


def test_ctrl_or_alt_arrows_do_not_move_it(qapp):
    o = make_overlay()
    move(o, (50, 50))
    key(o, Qt.Key_Right, Qt.ControlModifier)
    key(o, Qt.Key_Right, Qt.AltModifier)
    assert o.point() == QPoint(50, 50)


def test_without_a_mouse_enter_first_shows_the_pixel_it_would_pick(qapp):
    o = make_overlay()  # the pointer is on another monitor
    key(o, Qt.Key_Return)
    assert o.got == [] and o.point() == QPoint(LOGICAL[0] // 2, LOGICAL[1] // 2)  # the middle, shown
    key(o, Qt.Key_Right)
    key(o, Qt.Key_Return)
    assert [at(c) for c in o.got] == [(LOGICAL[0] // 2 + 1, LOGICAL[1] // 2)]


def test_the_arrow_keys_start_from_the_pointer_when_it_is_on_this_monitor(qapp):
    o = make_overlay()
    FakeCursor.at = o.geometry().topLeft() + QPoint(70, 90)
    key(o, Qt.Key_Down)
    assert o.point() == QPoint(70, 91)


def test_the_arrow_keys_start_from_the_exact_pointer_pixel_at_150_percent(qapp):
    """Qt's pointer position is in scaled pixels, rounded: 301 at 150% is 200.67, which it calls 201,
    and 201 x 1.5 is the pixel beside it. Windows' own position (physical pixels) is used instead."""
    o = make_overlay(1.5)
    FakeCursor.at = o.geometry().topLeft() + QPoint(301, 177)
    key(o, Qt.Key_Down)
    assert o.point() == QPoint(301, 178)


def test_esc_or_a_right_click_cancels(qapp):
    o = make_overlay()
    move(o, (10, 10))
    key(o, Qt.Key_Escape)
    click(o, (10, 10), Qt.RightButton)
    assert o.gave_up == [True, True] and o.got == []


def test_a_release_alone_or_one_on_another_monitor_picks_nothing(qapp):
    o = make_overlay()
    move(o, (10, 10))
    mouse(o, QEvent.MouseButtonRelease, (10, 10), Qt.LeftButton, Qt.NoButton)  # pressed elsewhere
    mouse(o, QEvent.MouseButtonPress, (10, 10))
    mouse(o, QEvent.MouseButtonRelease, (LOGICAL[0] + 50, 10), Qt.LeftButton, Qt.NoButton)  # let go on the next
    assert o.got == [] and o.gave_up == []
    click(o, (10, 10))  # and the next click still picks
    assert [at(c) for c in o.got] == [(10, 10)]


def test_closing_a_monitor_some_other_way_ends_the_pick(qapp):
    o = make_overlay()
    o.close()  # Alt+F4
    assert o.gave_up == [True]


def test_tab_changes_what_is_copied(qapp):
    o = make_overlay()
    said = []
    o.format_changed.connect(said.append)
    assert "color as HEX" in o.hint()
    move(o, (47, 91))
    assert o.label() == "#2F5B00"
    key(o, Qt.Key_Tab)
    assert o.copy_as == "rgb" and said == ["rgb"] and "color as RGB" in o.hint()
    assert o.label() == "rgb(47, 91, 0)"  # the loupe shows what will be copied
    key(o, Qt.Key_Tab)
    assert o.label() == "hsl(89, 100%, 18%)"
    key(o, Qt.Key_Backtab, Qt.ShiftModifier)
    key(o, Qt.Key_Backtab, Qt.ShiftModifier)
    assert o.copy_as == "hex" and said == ["rgb", "hsl", "rgb", "hex"]


def test_the_loupe_shows_each_pixel_as_a_crisp_square_with_the_one_picked_in_the_middle(qapp):
    o = make_overlay()
    move(o, (200, 150))
    shot = o.grab().toImage()
    box = o.loupe_rect()
    middle = loupe.cell_rect(box.topLeft(), 0, 0, radius=o.RADIUS, cell=o.CELL).center().toPoint()
    right = loupe.cell_rect(box.topLeft(), 1, 0, radius=o.RADIUS, cell=o.CELL).center().toPoint()
    above = loupe.cell_rect(box.topLeft(), 0, -2, radius=o.RADIUS, cell=o.CELL).center().toPoint()
    assert shot.pixelColor(middle) == coded(200, 150)
    assert shot.pixelColor(right) == coded(201, 150)
    assert shot.pixelColor(above) == coded(200, 148)


def test_past_the_edge_of_the_monitor_the_loupe_shows_its_backdrop_not_black(qapp):
    o = make_overlay()
    move(o, (0, 0))
    shot = o.grab().toImage()
    box = o.loupe_rect()
    outside = loupe.cell_rect(box.topLeft(), -1, -1, radius=o.RADIUS, cell=o.CELL).center().toPoint()
    corner = loupe.cell_rect(box.topLeft(), 0, 0, radius=o.RADIUS, cell=o.CELL).center().toPoint()
    assert shot.pixelColor(outside) == loupe.BACKDROP and shot.pixelColor(corner) == coded(0, 0)


def test_the_screen_is_shown_as_it_is_not_dimmed(qapp):
    o = make_overlay()
    move(o, (500, 300))
    assert o.grab().toImage().pixelColor(20, 20) == coded(20, 20)  # true colors to pick by eye


@pytest.mark.parametrize("pos", [(2, 2), (LOGICAL[0] - 2, 2), (2, LOGICAL[1] - 2), (LOGICAL[0] - 2, LOGICAL[1] - 2)])
def test_the_loupe_stays_on_the_monitor(qapp, pos):
    o = make_overlay()
    move(o, pos)
    assert o.rect().contains(o.loupe_rect())


def test_screen_readers_hear_what_it_is_and_its_keys(qapp):
    o = make_overlay()
    assert o.accessibleName() == "Pick a color"
    description = o.accessibleDescription()
    for words in ("arrow keys", "Enter", "Tab", "Esc"):
        assert words in description


# --- the picker on every monitor -----------------------------------------------------------------

@pytest.fixture
def monitors(monkeypatch):
    """Two monitors, at 100% and 150%, each "grabbing" a coded screen."""
    from snipforge import color_picker
    scales = iter([1.0, 1.5] * 20)
    primary = QGuiApplication.primaryScreen()
    monkeypatch.setattr(color_picker.QGuiApplication, "screens", staticmethod(lambda: [primary, primary]))
    monkeypatch.setattr(color_picker, "_grab_screen", lambda screen: frozen_pixmap(next(scales)))


def start_session():
    from snipforge.color_picker import ColorPickSession
    results = []
    session = ColorPickSession(results.append)
    session.start()
    for overlay in session.overlays:
        overlay.resize(*LOGICAL)
    return session, results


def test_every_monitor_freezes_and_picks_its_own_screen_pixels_at_its_own_scale(qapp, monitors):
    session, results = start_session()
    first, second = session.overlays
    assert session.focus is first and first.isVisible() and second.isVisible()
    click(second, (301 / 1.5, 177 / 1.5))
    assert [at(c) for c in results] == [(301, 177)]
    assert session.overlays == [] and not first.isVisible()


def test_esc_ends_the_pick_with_nothing(qapp, monitors):
    session, results = start_session()
    key(session.focus, Qt.Key_Escape)
    assert results == [None] and session.overlays == [] and session.focus is None


def test_a_pick_ends_even_when_a_monitor_is_already_gone(qapp, monitors):
    import shiboken6
    session, results = start_session()
    shiboken6.delete(session.overlays[1])  # e.g. Windows destroyed it with a display change
    session.cancel()
    assert results == [None] and session.overlays == []


def test_the_keyboard_follows_the_pointer_to_another_monitor(qapp, monitors):
    session, _results = start_session()
    first, second = session.overlays
    move(first, (40, 40))
    QApplication.sendEvent(second, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    assert session.focus is second
    assert first.point() is None  # nothing stays aimed at the monitor the pointer left
    session.cancel()


def test_tab_on_one_monitor_changes_every_monitor_and_is_remembered(qapp, monitors):
    session, _results = start_session()
    key(session.focus, Qt.Key_Tab)
    assert [o.copy_as for o in session.overlays] == ["rgb", "rgb"]
    assert prefs.color_format() == "rgb"
    session.cancel()


def test_the_picker_starts_with_the_remembered_format(qapp, monitors):
    prefs.set_color_format("hsl")
    session, _results = start_session()
    assert [o.copy_as for o in session.overlays] == ["hsl", "hsl"]
    session.cancel()


# --- after a pick --------------------------------------------------------------------------------

def test_a_picked_color_is_copied_added_to_recent_colors_and_announced(qapp, monitors):
    from snipforge.color_picker import ColorPicker
    notes = []
    picker = ColorPicker(notes.append, delay_ms=0)
    picker.start()
    click(picker.session.focus, (47, 91))  # the coded screen's color there is #2F5B00
    assert QApplication.clipboard().text() == "#2F5B00"
    assert colors.recent()[0] == "#2f5b00"  # in the editor's RECENT swatches
    assert notes == ["Copied #2F5B00\nRGB 47, 91, 0"]
    assert picker.session is None


def test_the_color_is_copied_in_the_format_chosen(qapp, monitors):
    from snipforge.color_picker import ColorPicker
    prefs.set_color_format("rgb")
    notes = []
    picker = ColorPicker(notes.append, delay_ms=0)
    picker.start()
    click(picker.session.focus, (47, 91))
    assert QApplication.clipboard().text() == "rgb(47, 91, 0)"
    assert notes == ["Copied rgb(47, 91, 0)\nHEX #2F5B00"]


def test_cancelling_copies_and_says_nothing(qapp, monitors):
    from snipforge.color_picker import ColorPicker
    QApplication.clipboard().setText("kept")
    notes = []
    picker = ColorPicker(notes.append, delay_ms=0)
    picker.start()
    key(picker.session.focus, Qt.Key_Escape)
    assert QApplication.clipboard().text() == "kept" and notes == [] and picker.session is None


def test_the_screen_freezes_a_moment_after_asking_so_the_tray_menu_has_closed(qapp, monitors):
    from PySide6.QtTest import QTest

    from snipforge.color_picker import START_DELAY_MS, ColorPicker
    picker = ColorPicker(lambda _text: None)
    picker.start()
    assert picker.session is not None and picker.session.overlays == []  # not yet
    QTest.qWait(START_DELAY_MS + 100)
    assert len(picker.session.overlays) == 2
    picker.cancel()


def test_a_pick_cancelled_before_the_screen_froze_never_freezes_it(qapp, monitors):
    from PySide6.QtTest import QTest

    from snipforge.color_picker import START_DELAY_MS, ColorPicker
    picker = ColorPicker(lambda _text: None)
    picker.start()
    session = picker.session
    picker.cancel()  # e.g. a snip started meanwhile
    QTest.qWait(START_DELAY_MS + 100)
    assert picker.session is None and session.overlays == []


def test_starting_again_while_picking_does_nothing(qapp, monitors):
    from snipforge.color_picker import ColorPicker
    picker = ColorPicker(lambda _text: None, delay_ms=0)
    picker.start()
    session = picker.session
    picker.start()
    assert picker.session is session and len(session.overlays) == 2
    picker.cancel()
    assert picker.session is None


def test_a_picker_that_cannot_start_ends_cleanly_and_says_so(qapp, monkeypatch):
    from snipforge import color_picker

    def broken(_screen):
        raise RuntimeError("no screen")

    monkeypatch.setattr(color_picker, "_grab_screen", broken)
    notes = []
    picker = color_picker.ColorPicker(notes.append, delay_ms=0)
    picker.start()
    assert picker.session is None and notes and "couldn't start" in notes[0]
    picker.start()  # and the next one isn't blocked
    assert len(notes) == 2


# --- where it starts: the tray, a shortcut of your own, Settings ---------------------------------

def test_pick_a_color_is_a_shortcut_with_no_combination_until_one_is_chosen():
    assert shortcuts.ACTIONS["color"] == "Pick a color"
    assert "color" in prefs.SHORTCUT_ACTIONS
    assert prefs.shortcut("color") == ""


def test_its_shortcut_is_registered_with_windows_not_the_keyboard_hook(qapp):
    api = FakeApi()
    keys = shortcuts.GlobalShortcuts(api=api)
    fired = []
    keys.triggered.connect(fired.append)
    assert keys.set("color", "Ctrl+Shift+F8") is None
    hotkey_id = FIRST_ID + list(shortcuts.ACTIONS).index("color")
    assert api.registered[hotkey_id] == (MOD_CONTROL | MOD_SHIFT, 0x77)
    assert keys._hotkey(hotkey_id) and fired == ["color"]
    keys.close()


@pytest.fixture
def forge(qapp, tmp_path, monkeypatch, no_dialogs, monitors):
    from snipforge import app as app_module
    from snipforge.library import Library
    monkeypatch.setattr(app_module.HotkeyListener, "start", lambda self: True)
    monkeypatch.setattr(app_module.HotkeyListener, "stop", lambda self: None)
    monkeypatch.setattr(app_module.UpdateFlow, "start", lambda self: None)
    f = app_module.SnipForgeApp(qapp, Library(tmp_path))
    yield f
    if f._color_picker is not None:
        f._color_picker.cancel()
    f._tray.hide()


def test_the_tray_and_a_shortcut_of_your_own_pick_a_color(forge):
    actions = {a.text(): a for a in forge._menu.actions()}
    actions["Pick a color"].trigger()
    assert forge._color_picker.session is not None
    forge._color_picker.cancel()
    forge._on_shortcut("color")
    assert forge._color_picker.session is not None


def test_no_color_picking_over_a_snip_being_chosen(forge, monkeypatch):
    notes = []
    monkeypatch.setattr(forge, "_note", notes.append)
    forge._session = object()  # a snip under way
    forge.pick_color()
    assert forge._color_picker is None or forge._color_picker.session is None
    assert notes and "snip" in notes[0]
    forge._session = None


def test_a_snip_starting_cancels_a_pick(forge, monkeypatch):
    from snipforge import app as app_module
    monkeypatch.setattr(app_module.QTimer, "singleShot", lambda *_args: None)  # the snip itself never starts
    forge.pick_color()
    assert forge._color_picker.session is not None
    forge.capture()
    assert forge._color_picker.session is None
    forge._session = None


@pytest.fixture
def settings(qapp, tmp_path):
    from snipforge.settings_window import SettingsWindow
    keys = shortcuts.GlobalShortcuts(api=FakeApi())
    w = SettingsWindow(library_root=tmp_path, shortcuts=keys)
    yield w
    w.close()
    keys.close()


def test_settings_set_its_shortcut_and_what_it_copies(settings):
    settings.show_section("capture")
    edit = settings.shortcut_edits["color"]
    assert edit.isVisibleTo(settings) and edit.accessibleName() == "Shortcut for Pick a color"
    edit.changed.emit("Ctrl+Shift+F8")
    assert prefs.shortcut("color") == "Ctrl+Shift+F8"
    choice = settings.color_format
    assert choice.isVisibleTo(settings) and choice.accessibleName() == "Copy picked colors as"
    assert choice.value == "hex"
    choice.button("hsl").click()
    assert prefs.color_format() == "hsl"
