"""The capture overlay's Record switch: choose an area to record a video of, instead of snipping it."""
import pytest
from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPixmap
from PySide6.QtTest import QTest

from snipforge import capture, prefs
from snipforge.settings import app_settings
from tests.test_capture_toolbar import WINDOW, click, drag, key, mouse


@pytest.fixture(autouse=True)
def clean_prefs():
    for name in ("capture_mode", "capture_delay"):
        app_settings().remove(prefs.KEYS[name])
    yield
    for name in ("capture_mode", "capture_delay"):
        app_settings().remove(prefs.KEYS[name])


def make_overlay(record=True, recordable=True, mode="area"):
    screen = QGuiApplication.primaryScreen()
    pix = QPixmap(screen.geometry().size())
    pix.fill(QColor("#e8eaee"))
    o = capture._ScreenOverlay(screen, pix, [WINDOW], mode=mode, toolbar=True, record=record,
                               recordable=recordable)
    o.resize(screen.geometry().size())
    o.got = []
    o.selected.connect(o.got.append)
    o.area_chosen.connect(lambda rect: o.got.append(("area", rect)))
    return o


def origin() -> QPoint:
    return QGuiApplication.primaryScreen().geometry().topLeft()


def test_in_record_mode_a_drag_chooses_an_area_to_record_not_a_snip(qapp):
    o = make_overlay()

    drag(o, (50, 60), (450, 360))

    assert len(o.got) == 1 and o.got[0][0] == "area"  # an area, not an image
    rect = o.got[0][1]
    assert rect.topLeft() == origin() + QPoint(50, 60) and (rect.width(), rect.height()) == (400, 300)
    o.close()


def test_in_record_mode_a_click_records_that_window(qapp):
    o = make_overlay()

    click(o, (WINDOW.center().x(), WINDOW.center().y()))

    assert o.got == [("area", WINDOW)]  # window rects are in desktop coordinates already
    o.close()


def test_in_record_mode_enter_records_the_whole_monitor(qapp):
    o = make_overlay()

    key(o, Qt.Key_Return)

    assert o.got == [("area", QRect(origin(), o.size()))]
    o.close()


def test_an_area_too_small_to_record_is_refused_and_says_why(qapp):
    o = make_overlay()

    drag(o, (50, 60), (90, 200))  # 41 pixels wide

    assert o.got == []
    assert "too small to record" in o.hint()
    o.close()


def test_r_switches_between_snipping_and_recording_and_the_toolbar_follows(qapp):
    o = make_overlay(record=False)
    bar = o.toolbar
    assert not o.record and not bar.record_button.isChecked()

    key(o, Qt.Key_R, "r")

    assert o.record and bar.record_button.isChecked()
    assert bar.delay_button.isHidden() and bar.pointer_button.isHidden()  # a snip's delay and pointer
    assert "record" in o.hint().lower()
    QTest.mouseClick(bar.record_button, Qt.LeftButton)
    assert not o.record and not bar.delay_button.isHidden()
    o.close()


def test_recording_leaves_scrolling_mode(qapp):
    o = make_overlay(record=False, mode="scroll")
    if o.mode != "scroll":
        pytest.skip("scrolling capture isn't available here")

    o.set_record(True)

    assert o.mode == "area" and o.toolbar.buttons["scroll"].isHidden()
    o.close()


def test_no_record_switch_where_recording_isnt_offered(qapp):
    o = make_overlay(record=False, recordable=False)

    key(o, Qt.Key_R, "r")

    assert not o.record and o.toolbar.record_button.isHidden()
    o.close()


def test_delay_and_pointer_keys_do_nothing_while_choosing_what_to_record(qapp):
    o = make_overlay()
    pointer_before = o.include_pointer

    key(o, Qt.Key_D, "d")
    key(o, Qt.Key_P, "p")

    assert o.toolbar.delay_menu.isHidden() and o.include_pointer == pointer_before
    o.close()


def test_fixed_size_mode_records_its_frame(qapp):
    o = make_overlay(mode="fixed")
    o.set_fixed_size(640, 480)
    mouse(o, QEvent.MouseMove, (400, 300), Qt.NoButton, Qt.NoButton)

    key(o, Qt.Key_Return)

    assert len(o.got) == 1 and o.got[0][0] == "area"
    assert (o.got[0][1].width(), o.got[0][1].height()) == (640, 480)
    o.close()


# --- the capture session --------------------------------------------------------------------------

def test_a_recording_session_hands_over_the_area_and_ends(qapp):
    prefs.set_capture_delay(3)  # a snip's delay: recording has its own countdown, after the area
    done, areas = [], []
    session = capture.CaptureSession(done.append, on_area=lambda rect, screen: areas.append((rect, screen)),
                                     record=True)
    session.start()
    try:
        assert session.countdown is None and session._overlays
        assert all(o.record for o in session._overlays)
        overlay = session._overlays[0]
        drag(overlay, (50, 60), (450, 360))
        assert done == [None]  # the snip part is over...
        assert len(areas) == 1 and areas[0][1] is overlay.capture_screen  # ...and the area goes to record
        assert session._overlays == []
    finally:
        session._finish(None)


def test_the_record_switch_keeps_every_monitor_in_step(qapp):
    session = capture.CaptureSession(lambda image: None, on_area=lambda rect, screen: None)
    session.start()
    try:
        first = session._overlays[0]
        key(first, Qt.Key_R, "r")
        assert all(o.record for o in session._overlays)
    finally:
        session._finish(None)


def test_a_session_without_recording_offers_no_switch(qapp):
    session = capture.CaptureSession(lambda image: None)
    session.start()
    try:
        assert all(o.toolbar.record_button.isHidden() for o in session._overlays)
    finally:
        session._finish(None)


def test_the_overlay_knows_the_recorders_smallest_area():
    from snipforge.video.recorder import MIN_SIDE

    assert capture.MIN_RECORD_SIDE == MIN_SIDE
