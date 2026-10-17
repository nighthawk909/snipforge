"""Fixed size mode: a frame of an exact size follows the pointer, and a click snips it."""
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeyEvent, QMouseEvent, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from snipforge import prefs
from snipforge.settings import app_settings

WINDOW = QRect(100, 80, 300, 200)


class FakeCursor:
    """Where the overlay thinks the pointer is (the real one is never read or moved)."""
    at = QPoint(-30000, -30000)  # off every monitor, unless a test puts it somewhere

    @classmethod
    def pos(cls, *_args) -> QPoint:
        return cls.at


@pytest.fixture(autouse=True)
def fake_cursor(monkeypatch):
    from snipforge import capture
    FakeCursor.at = QPoint(-30000, -30000)
    monkeypatch.setattr(capture, "QCursor", FakeCursor)


@pytest.fixture(autouse=True)
def clean_prefs():
    keys = [prefs.KEYS["capture_mode"], prefs.KEYS["capture_fixed_size"], prefs.KEYS["capture_delay"]]
    for k in keys:
        app_settings().remove(k)
    yield
    for k in keys:
        app_settings().remove(k)


def mouse(widget, etype, pos, button=Qt.LeftButton, buttons=Qt.LeftButton):
    pt = QPointF(*pos)
    QApplication.sendEvent(widget, QMouseEvent(etype, pt, widget.mapToGlobal(pt), button, buttons, Qt.NoModifier))


def move(widget, pos):
    mouse(widget, QEvent.MouseMove, pos, Qt.NoButton, Qt.NoButton)


def click(widget, pos):
    move(widget, pos)
    mouse(widget, QEvent.MouseButtonPress, pos)
    mouse(widget, QEvent.MouseButtonRelease, pos, buttons=Qt.NoButton)


def key(widget, k, modifiers=Qt.NoModifier, text=""):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyPress, k, modifiers, text))


def coded_screen(width: int, height: int) -> QImage:
    """Every pixel says where it is (red and blue's high half: x; green and blue's low half: y), so
    a snip shows where it was taken from. Columns, then rows added on top: fast, and exact."""
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(Qt.black)
    p = QPainter(image)
    for x in range(width):
        p.setPen(QColor(x & 255, 0, (x >> 8) << 4))
        p.drawLine(x, 0, x, height - 1)
    p.setCompositionMode(QPainter.CompositionMode_Plus)
    for y in range(height):
        p.setPen(QColor(0, y & 255, y >> 8))
        p.drawLine(0, y, width - 1, y)
    p.end()
    return image


def where(image: QImage) -> tuple[int, int]:
    """The screen position of a snip's top-left pixel."""
    rgb = image.pixel(0, 0)
    red, green, blue = rgb >> 16 & 255, rgb >> 8 & 255, rgb & 255
    return (blue >> 4) << 8 | red, (blue & 15) << 8 | green


LOGICAL = (1000, 700)  # the monitor in these tests (in Windows' scaled pixels)
_SCREENS: dict[tuple[int, int], QImage] = {}


def make_overlay(size=(200, 100), dpr=1.0, mode="fixed", toolbar=False):
    from snipforge.capture import _ScreenOverlay
    physical = (round(LOGICAL[0] * dpr), round(LOGICAL[1] * dpr))
    if physical not in _SCREENS:
        _SCREENS[physical] = coded_screen(*physical)
    pix = QPixmap.fromImage(_SCREENS[physical])
    pix.setDevicePixelRatio(dpr)
    o = _ScreenOverlay(QGuiApplication.primaryScreen(), pix, [WINDOW], mode=mode, toolbar=toolbar,
                       fixed_size=size)
    o.resize(*LOGICAL)
    o.got = []
    o.selected.connect(o.got.append)
    return o


def test_the_coded_screen_says_where_each_pixel_is():
    image = coded_screen(1800, 1300)
    for x, y in ((0, 0), (37, 300), (255, 256), (1799, 1299), (1024, 777)):
        assert where(image.copy(x, y, 3, 3)) == (x, y)


# --- the remembered size -----------------------------------------------------------------------

def test_fixed_size_is_remembered_and_validated():
    assert prefs.capture_fixed_size() == prefs.DEFAULT_FIXED_SIZE
    prefs.set_capture_fixed_size(1280, 720)
    assert prefs.capture_fixed_size() == (1280, 720)
    for junk in ("huge", "5x5", "800x", "-3x400", "100000x100"):
        app_settings().setValue(prefs.KEYS["capture_fixed_size"], junk)
        assert prefs.capture_fixed_size() == prefs.DEFAULT_FIXED_SIZE, junk
    for width, height in ((5, 400), (400, prefs.MAX_FIXED + 1)):
        with pytest.raises(ValueError):
            prefs.set_capture_fixed_size(width, height)


def test_fixed_size_is_a_mode_the_toolbar_and_prefs_know():
    from snipforge.capture_toolbar import MODES
    assert "fixed" in prefs.CAPTURE_MODES and MODES["fixed"].label == "Fixed size" and MODES["fixed"].key == "X"
    prefs.set_capture_mode("fixed")
    assert prefs.capture_mode() == "fixed"


# --- the frame ---------------------------------------------------------------------------------

def test_the_frame_follows_the_pointer_and_a_click_snips_exactly_that_size(qapp):
    o = make_overlay((200, 100))
    move(o, (400, 300))
    assert o._selection() == QRect(300, 250, 200, 100)
    move(o, (500, 320))
    assert o._selection() == QRect(400, 270, 200, 100)
    click(o, (400, 300))
    assert o.got[0].size().toTuple() == (200, 100) and where(o.got[0]) == (300, 250)


def test_the_frame_stays_on_the_monitor_at_its_edges(qapp):
    o = make_overlay((200, 100))
    w, h = o.width(), o.height()
    click(o, (5, 5))
    click(o, (w - 2, h - 2))
    assert where(o.got[0]) == (0, 0)
    assert where(o.got[1]) == (w - 200, h - 100) and o.got[1].size().toTuple() == (200, 100)


@pytest.mark.parametrize("dpr", [1.25, 1.5, 1.75])
@pytest.mark.parametrize("size", [(800, 600), (801, 599), (333, 17)])
def test_the_size_is_exact_in_screen_pixels_at_any_display_scale(qapp, dpr, size):
    o = make_overlay(size, dpr=dpr)
    click(o, (400, 300))
    x, y = where(o.got[0])
    assert o.got[0].size().toTuple() == size
    assert abs(x + size[0] / 2 - 400 * dpr) <= 1 and abs(y + size[1] / 2 - 300 * dpr) <= 1  # centred on the pointer


def test_a_frame_bigger_than_the_monitor_takes_all_of_it(qapp):
    o = make_overlay((prefs.MAX_FIXED, prefs.MAX_FIXED))
    click(o, (400, 300))
    assert o.got[0].size() == o._frozen_image.size() and where(o.got[0]) == (0, 0)


def test_arrow_keys_nudge_the_frame_and_enter_snips_it(qapp):
    o = make_overlay((200, 100))
    move(o, (400, 300))
    key(o, Qt.Key_Right)
    key(o, Qt.Key_Right)
    key(o, Qt.Key_Down, Qt.ShiftModifier)  # Shift: ten pixels
    key(o, Qt.Key_Left, Qt.KeypadModifier)  # the number pad's arrows too
    key(o, Qt.Key_Return)
    assert where(o.got[0]) == (301, 260) and o.got[0].size().toTuple() == (200, 100)


def test_arrow_keys_stop_at_the_edges(qapp):
    o = make_overlay((200, 100))
    move(o, (100, 50))
    for _ in range(3):
        key(o, Qt.Key_Up, Qt.ShiftModifier)
        key(o, Qt.Key_Left)
    key(o, Qt.Key_Enter)
    assert where(o.got[0]) == (0, 0)


def test_enter_never_snips_a_frame_you_have_not_seen(qapp):
    o = make_overlay((200, 100))  # the pointer is on another monitor: no frame here yet
    assert o._selection() is None
    key(o, Qt.Key_Return)  # first Enter: draws it, in the middle
    assert o.got == [] and o._selection() == QRect(400, 300, 200, 100)
    key(o, Qt.Key_Return)  # second Enter: snips it
    assert where(o.got[0]) == (400, 300)


def test_the_frame_is_there_from_the_start_under_the_pointer(qapp):
    from snipforge import capture
    o = make_overlay((200, 100))
    capture.QCursor.at = o.geometry().topLeft() + QPoint(300, 200)
    o.show()
    assert o._selection() == QRect(200, 150, 200, 100)
    key(o, Qt.Key_Return)
    assert where(o.got[0]) == (200, 150)


def test_x_before_the_mouse_moves_puts_the_frame_under_the_pointer(qapp):
    from snipforge import capture
    o = make_overlay((200, 100), mode="area")
    capture.QCursor.at = o.geometry().topLeft() + QPoint(150, 120)
    key(o, Qt.Key_X, text="x")
    assert o._selection() == QRect(50, 70, 200, 100)


def test_picking_fixed_size_on_the_toolbar_puts_the_frame_in_the_middle(qapp):
    from snipforge import capture
    o = shown(mode="area")
    bar = o.toolbar
    capture.QCursor.at = o.geometry().topLeft() + bar.buttons["fixed"].mapTo(o, QPoint(5, 5))
    bar.buttons["fixed"].click()  # not centred up under the toolbar, where the pointer is
    assert o._selection() == QRect(400, 300, 200, 100)


def test_ctrl_or_alt_arrows_do_not_nudge(qapp):
    o = make_overlay((200, 100))
    move(o, (400, 300))
    key(o, Qt.Key_Right, Qt.ControlModifier)
    key(o, Qt.Key_Down, Qt.AltModifier)
    assert o._selection() == QRect(300, 250, 200, 100)


def test_arrow_keys_do_nothing_in_other_modes(qapp):
    o = make_overlay((200, 100), mode="area")
    move(o, (400, 300))
    key(o, Qt.Key_Right)
    key(o, Qt.Key_Return)
    assert o.got[0].size() == o.size()  # Enter is still the whole monitor


def test_x_switches_to_fixed_size_and_shows_the_frame_at_once(qapp):
    o = make_overlay((200, 100), mode="area")
    switched = []
    o.mode_changed.connect(switched.append)
    move(o, (400, 300))
    key(o, Qt.Key_X, text="x")
    assert switched == ["fixed"] and o._selection() == QRect(300, 250, 200, 100)


def test_fixed_size_explains_itself_and_has_no_magnifier(qapp):
    o = make_overlay()
    assert "arrow keys" in o.hint() and "A W F X" in o.hint()
    assert not o._shows_magnifier()
    o.set_mode("area")
    assert o._shows_magnifier() == prefs.magnifier()
    assert "X for a fixed size" in o.accessibleDescription()


def test_the_size_label_shows_the_exact_size(qapp):
    o = make_overlay((801, 599), dpr=1.5)
    move(o, (400, 300))
    assert o._size_text(o._selection()) == "801 × 599"


# --- the size menu -----------------------------------------------------------------------------

def shown(**kwargs):
    o = make_overlay(toolbar=True, **kwargs)
    o.show()
    QApplication.processEvents()
    return o


def test_the_size_button_shows_only_in_fixed_size_and_the_mode_buttons_stay_put(qapp):
    o = shown(mode="area")
    bar = o.toolbar
    before = {k: b.mapTo(o, QPoint()) for k, b in bar.buttons.items()}
    assert bar.size_button.isHidden()
    bar.buttons["fixed"].click()
    QApplication.processEvents()
    assert not bar.size_button.isHidden() and o.mode == "fixed"
    assert {k: b.mapTo(o, QPoint()) for k, b in bar.buttons.items()} == before  # nothing slid under the pointer
    assert bar.geometry().right() < o.width()
    bar.buttons["window"].click()
    QApplication.processEvents()
    assert bar.size_button.isHidden() and {k: b.mapTo(o, QPoint()) for k, b in bar.buttons.items()} == before


def test_the_size_button_names_the_size_and_offers_presets(qapp):
    o = shown(size=(800, 600))
    bar = o.toolbar
    assert "800 × 600" in bar.size_button.text()
    labels = [a.text() for a in bar.size_menu.actions() if a.text()]
    assert any("1920 × 1080" in t for t in labels) and any("640 × 480" in t for t in labels)
    checked = [a.text() for a in bar.size_menu.actions() if a.isChecked()]
    assert len(checked) == 1 and "800 × 600" in checked[0]
    assert bar.size_button.accessibleName() == "Size"


def test_picking_a_preset_resizes_the_frame_around_its_middle_and_is_announced(qapp):
    o = shown(size=(200, 100))
    picked = []
    o.size_changed.connect(lambda w, h: picked.append((w, h)))
    move(o, (400, 300))
    preset = next(a for a in o.toolbar.size_menu.actions() if a.text().startswith("640 × 480"))
    preset.trigger()
    assert picked == [(640, 480)] and o._selection() == QRect(80, 60, 640, 480)
    assert "640 × 480" in o.toolbar.size_button.text()


def test_a_custom_size_can_be_typed_into_the_size_menu(qapp):
    o = shown(size=(200, 100))
    picked = []
    o.size_changed.connect(lambda w, h: picked.append((w, h)))
    custom = o.toolbar.custom_size
    custom.width_box.setValue(333)
    custom.height_box.setValue(222)
    custom.use_button.click()
    assert picked == [(333, 222)]
    assert not any(a.isChecked() for a in o.toolbar.size_menu.actions())  # not one of the presets
    assert "333 × 222" in o.toolbar.size_button.text()
    custom.width_box.setValue(400)
    QApplication.sendEvent(custom.width_box.lineEdit(), QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    assert picked[-1] == (400, 222)


def test_custom_size_boxes_are_labelled_and_kept_in_range(qapp):
    o = shown()
    custom = o.toolbar.custom_size
    assert custom.width_box.accessibleName() == "Width in pixels"
    assert custom.height_box.accessibleName() == "Height in pixels"
    assert (custom.width_box.minimum(), custom.width_box.maximum()) == (prefs.MIN_FIXED, prefs.MAX_FIXED)


def test_the_custom_row_works_from_the_keyboard_alone(qapp):
    from snipforge.capture_toolbar import CaptureToolbar
    host = QWidget()
    host.resize(1000, 200)
    bar = CaptureToolbar("fixed", host, fixed_size=(640, 480))
    host.show()
    QTest.qWaitForWindowExposed(host)
    picked = []
    bar.size_picked.connect(lambda w, h: picked.append((w, h)))
    bar.size_menu.popup(bar.size_button.mapToGlobal(QPoint(0, bar.size_button.height())))
    QTest.qWait(50)

    def typing(*keys):
        for k in keys:
            popup = QApplication.activePopupWidget()
            target = popup.focusWidget() or popup
            if isinstance(k, str):
                QTest.keyClicks(target, k)
            else:
                QTest.keyClick(target, k)

    try:
        typing(Qt.Key_Up)  # up from the top wraps to the last row: Custom
        assert bar.size_menu.focusWidget() is bar.custom_size.width_box
        clear = (Qt.Key_End, Qt.Key_Backspace, Qt.Key_Backspace, Qt.Key_Backspace)
        typing(*clear, "500", Qt.Key_Tab)  # Tab: the height box, not the menu's next row
        assert bar.size_menu.focusWidget() is bar.custom_size.height_box
        typing(*clear, "300", Qt.Key_Return)
        assert picked == [(500, 300)] and not bar.size_menu.isVisible()
    finally:
        bar.size_menu.close()
        host.close()


def test_enter_on_use_picks_the_size(qapp):
    o = shown(size=(200, 100))
    picked = []
    o.size_changed.connect(lambda w, h: picked.append((w, h)))
    custom = o.toolbar.custom_size
    custom.width_box.setValue(321)
    QApplication.sendEvent(custom.use_button, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    assert picked == [(321, 100)]


def test_a_number_below_the_smallest_size_means_the_smallest(qapp):
    o = shown(size=(200, 100))
    picked = []
    o.size_changed.connect(lambda w, h: picked.append((w, h)))
    box = o.toolbar.custom_size.width_box
    QTest.keyClick(box, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(box, "5")
    o.toolbar.custom_size.use_button.click()
    assert picked == [(prefs.MIN_FIXED, 100)]


def test_an_emptied_box_keeps_its_size(qapp):
    o = shown(size=(200, 100))
    picked = []
    o.size_changed.connect(lambda w, h: picked.append((w, h)))
    box = o.toolbar.custom_size.width_box
    QTest.keyClick(box, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClick(box, Qt.Key_Backspace)
    QTest.keyClick(box, Qt.Key_Return)
    assert picked == [(200, 100)]


def test_reopening_the_size_menu_starts_fresh(qapp):
    o = shown(size=(640, 480))
    bar = o.toolbar
    bar.open_size_menu()
    QTest.qWait(30)
    bar.custom_size.width_box.setFocus()
    bar.custom_size.width_box.setValue(999)  # typed, then Esc
    bar.size_menu.close()
    bar.open_size_menu()
    QTest.qWait(30)
    try:
        assert bar.custom_size.width_box.value() == 640
        assert bar.size_menu.focusWidget() is not bar.custom_size.width_box
        QTest.keyClick(bar.size_menu, Qt.Key_Down)  # the arrows move through the menu, not a number
        assert bar.custom_size.width_box.value() == 640
        assert bar.size_menu.activeAction() in bar._size_actions.values()
    finally:
        bar.size_menu.close()


def wait_for(condition, ms: int = 1000) -> bool:
    """Run Qt's event loop until `condition()` holds, for up to `ms` milliseconds."""
    for _ in range(ms // 10):
        if condition():
            return True
        QTest.qWait(10)
    return bool(condition())


def take_keyboard(o):
    o.activateWindow()
    o.setFocus()
    QTest.qWaitForWindowActive(o)
    assert wait_for(lambda: QApplication.focusWidget() is o, 1000)


def test_x_again_opens_the_size_menu_and_a_pick_hands_the_keyboard_back(qapp):
    o = shown(size=(200, 100))
    take_keyboard(o)
    move(o, (400, 300))
    key(o, Qt.Key_X, text="x")
    menu = o.toolbar.size_menu
    assert wait_for(menu.isVisible, 1000)
    QTest.keyClick(menu, Qt.Key_Down)  # the first preset: 640 × 480
    QTest.keyClick(menu, Qt.Key_Return)
    assert not menu.isVisible() and o.fixed_size == (640, 480)
    assert wait_for(lambda: QApplication.focusWidget() is o, 1000)  # so arrows and Enter work
    QTest.keyClick(o, Qt.Key_Right)
    QTest.keyClick(o, Qt.Key_Return)
    assert where(o.got[0]) == (81, 60) and o.got[0].size().toTuple() == (640, 480)


def test_tab_goes_round_the_toolbar_and_back_to_the_frame(qapp):
    o = shown()
    take_keyboard(o)
    seen = []
    for _ in range(15):
        QTest.keyClick(QApplication.focusWidget(), Qt.Key_Tab)
        seen.append(QApplication.focusWidget())
        if seen[-1] is o:
            break
    assert seen[-1] is o and o.toolbar.size_button in seen


def test_a_new_size_never_moves_the_toolbar_buttons(qapp):
    o = shown(size=(16, 16))
    bar = o.toolbar

    def places():
        return [b.mapTo(o, QPoint()) for b in (*bar.buttons.values(), bar.delay_button, bar.cancel_button)]

    before = places()
    for size in ((640, 480), (1920, 1080), (prefs.MAX_FIXED, prefs.MAX_FIXED)):
        o.set_fixed_size(*size)
        QApplication.processEvents()
        assert places() == before, size


def test_the_frame_stays_while_the_size_menu_is_open(qapp):
    o = shown(size=(200, 100))
    move(o, (400, 300))
    o.toolbar.open_size_menu()
    assert wait_for(o.toolbar.size_menu.isVisible, 1000)
    try:
        QApplication.sendEvent(o, QEvent(QEvent.Leave))  # the pointer went into the menu
        assert o._selection() == QRect(300, 250, 200, 100)
    finally:
        o.toolbar.size_menu.close()
    QApplication.sendEvent(o, QEvent(QEvent.Leave))  # really leaving (to another monitor) drops it
    assert o._selection() is None


def test_the_keyboard_follows_the_pointer_to_another_monitor(qapp):
    from PySide6.QtGui import QEnterEvent
    first, second = shown(size=(200, 100)), shown(size=(200, 100))
    taken = []
    second.keyboard_taken.connect(lambda: taken.append(second))
    take_keyboard(first)
    at = QPointF(400, 300)
    QApplication.sendEvent(second, QEnterEvent(at, at, second.mapToGlobal(at)))
    move(second, (400, 300))
    assert taken == [second]
    assert wait_for(lambda: QApplication.focusWidget() is second, 1000)
    QTest.keyClick(QApplication.focusWidget(), Qt.Key_Right)
    QTest.keyClick(QApplication.focusWidget(), Qt.Key_Return)
    assert first.got == [] and where(second.got[0]) == (301, 250)


# --- the session -------------------------------------------------------------------------------

def test_a_snip_starts_in_fixed_size_with_the_remembered_size(qapp, monkeypatch):
    from snipforge import capture
    monkeypatch.setattr(capture, "_grab_screen", lambda screen: _blank(screen))
    prefs.set_capture_mode("fixed")
    prefs.set_capture_fixed_size(320, 200)
    results = []
    session = capture.CaptureSession(results.append)
    session.start()
    try:
        overlay = session.focus
        assert overlay.mode == "fixed" and overlay.fixed_size == (320, 200)
        overlay.toolbar.custom_size.width_box.setValue(500)
        overlay.toolbar.custom_size.use_button.click()
        assert prefs.capture_fixed_size() == (500, 200)
        assert all(o.fixed_size == (500, 200) for o in session._overlays)
        other = make_overlay((500, 200))  # stands in for a second monitor, with its frame still drawn
        move(other, (400, 300))
        session._overlays.append(other)
        session.focus = None
        overlay.keyboard_taken.emit()  # the pointer came to this monitor
        assert session.focus is overlay and other._selection() is None
        session._overlays.remove(other)
        click(overlay, (300, 300))
        screen = overlay._frozen_image.size()
        assert results[0].size().toTuple() == (min(500, screen.width()), min(200, screen.height()))
    finally:
        session._finish(None)


def _blank(screen) -> QPixmap:
    dpr = screen.devicePixelRatio()
    pix = QPixmap(round(screen.geometry().width() * dpr), round(screen.geometry().height() * dpr))
    pix.fill(Qt.white)
    pix.setDevicePixelRatio(dpr)
    return pix
