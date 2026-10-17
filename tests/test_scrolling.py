"""Scrolling capture: the runner that scrolls, grabs and stitches, and the Scrolling mode around it."""
import sys
import time

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeyEvent, QMouseEvent, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snipforge import prefs
from snipforge.settings import app_settings
from tests.test_stitch import page, text_page

VIEW_H = 200
PX_PER_NOTCH = 30


@pytest.fixture(autouse=True)
def clean_prefs():
    for name in ("capture_mode", "capture_delay"):
        app_settings().remove(prefs.KEYS[name])
    yield
    for name in ("capture_mode", "capture_delay"):
        app_settings().remove(prefs.KEYS[name])


class FakeScroller:
    """Stands in for scroll_input: a page that scrolls PX_PER_NOTCH per notch, and a pointer."""

    def __init__(self, full: QImage, moved_by_person_after: int | None = None):
        self.full = full
        self.top = 0
        self.position = QPoint(5, 5)
        self.placed = []
        self.notches = []
        self._moved_after = moved_by_person_after

    def available(self):
        return True

    def pointer_position(self):
        if self._moved_after is not None and len(self.notches) >= self._moved_after:
            return self.position + QPoint(40, 0)  # the person moved the mouse
        return self.position

    def place_pointer(self, at):
        self.position = QPoint(at)
        self.placed.append(QPoint(at))

    def scroll_down(self, notches):
        self.notches.append(notches)
        self.top = min(self.top + notches * PX_PER_NOTCH, self.full.height() - VIEW_H)

    def grab(self):
        return self.full.copy(QRect(0, self.top, self.full.width(), VIEW_H))


def run(capture, timeout=10.0):
    results = []
    capture.finished.connect(results.append)
    capture.start()
    deadline = time.time() + timeout
    while not results and time.time() < deadline:
        QTest.qWait(5)
    assert results, "the scrolling capture never finished"
    return results[0]


def make_capture(scroller, **kwargs):
    from snipforge.scrolling import ScrollCapture
    return ScrollCapture(QGuiApplication.primaryScreen(), QRect(0, 0, scroller.full.width(), VIEW_H),
                         grab=scroller.grab, scroller=scroller, settle_ms=0, **kwargs)


def same(a: QImage, b: QImage) -> bool:
    return a.size() == b.size() and a.convertToFormat(QImage.Format_RGB32) == b.convertToFormat(QImage.Format_RGB32)


# --- the runner ------------------------------------------------------------------------------------

def test_scrolls_to_the_end_and_stitches_the_whole_page(qapp):
    full = text_page(1400)
    scroller = FakeScroller(full)
    result = run(make_capture(scroller))
    assert same(result, full)
    assert scroller.position == QPoint(5, 5)  # the pointer goes back where it was


def test_scroll_steps_grow_to_most_of_the_view(qapp):
    scroller = FakeScroller(page(3000))
    run(make_capture(scroller))
    assert scroller.notches[0] <= 3  # starts small
    assert max(scroller.notches) * PX_PER_NOTCH <= VIEW_H * 0.75  # always overlaps the last view
    assert max(scroller.notches) >= 3  # then speeds up


def test_moving_the_mouse_stops_it_with_what_it_has(qapp):
    full = page(3000)
    scroller = FakeScroller(full, moved_by_person_after=3)
    result = run(make_capture(scroller))
    assert VIEW_H < result.height() < full.height()
    assert same(result, full.copy(QRect(0, 0, full.width(), result.height())))


def test_stop_keeps_what_it_has(qapp):
    from snipforge.scrolling import ScrollCapture
    full = page(3000)
    scroller = FakeScroller(full)
    capture = ScrollCapture(QGuiApplication.primaryScreen(), QRect(0, 0, full.width(), VIEW_H),
                            grab=scroller.grab, scroller=scroller, settle_ms=30)
    results = []
    capture.finished.connect(results.append)
    capture.start()
    QTest.qWait(100)
    capture.stop()
    QTest.qWait(50)
    assert len(results) == 1 and results[0] is not None and results[0].height() >= VIEW_H
    assert scroller.position == QPoint(5, 5)


def test_cancel_gives_nothing(qapp):
    from snipforge.scrolling import ScrollCapture
    scroller = FakeScroller(page(3000))
    capture = ScrollCapture(QGuiApplication.primaryScreen(), QRect(0, 0, 120, VIEW_H),
                            grab=scroller.grab, scroller=scroller, settle_ms=30)
    results = []
    capture.finished.connect(results.append)
    capture.start()
    QTest.qWait(60)
    capture.cancel()
    QTest.qWait(80)
    assert results == [None]


def test_gives_up_after_too_many_steps(qapp):
    scroller = FakeScroller(page(3000))
    result = run(make_capture(scroller, max_steps=4))
    assert len(scroller.notches) == 4 and result.height() > VIEW_H


def test_status_pill_shows_while_scrolling(qapp):
    from snipforge.scrolling import ScrollCapture
    scroller = FakeScroller(page(3000))
    capture = ScrollCapture(QGuiApplication.primaryScreen(), QRect(0, 0, 120, VIEW_H),
                            grab=scroller.grab, scroller=scroller, settle_ms=30)
    capture.start()
    QTest.qWait(10)
    assert capture.status is not None and capture.status.isVisible()
    assert "move the mouse to stop" in capture.status.text().lower()
    assert capture.status.windowFlags() & Qt.WindowDoesNotAcceptFocus
    capture.cancel()
    QTest.qWait(50)
    assert capture.status is None


# --- the Scrolling mode -----------------------------------------------------------------------------

def mouse(widget, etype, pos, button=Qt.LeftButton, buttons=Qt.LeftButton):
    pt = QPointF(*pos)
    QApplication.sendEvent(widget, QMouseEvent(etype, pt, widget.mapToGlobal(pt), button, buttons, Qt.NoModifier))


def make_overlay(mode="scroll", toolbar=True, scrolling=True):
    from snipforge.capture import _ScreenOverlay
    screen = QGuiApplication.primaryScreen()
    pix = QPixmap(screen.geometry().size())
    pix.fill(QColor("#e8eaee"))
    o = _ScreenOverlay(screen, pix, [QRect(100, 80, 300, 200)], mode=mode, toolbar=toolbar, scrolling=scrolling)
    o.resize(screen.geometry().size())
    o.got, o.scroll = [], []
    o.selected.connect(o.got.append)
    o.scroll_requested.connect(o.scroll.append)
    return o


def test_scrolling_mode_hands_over_the_area_instead_of_snipping(qapp):
    o = make_overlay()
    mouse(o, QEvent.MouseButtonPress, (10, 10))
    mouse(o, QEvent.MouseMove, (60, 40), Qt.NoButton)
    mouse(o, QEvent.MouseButtonRelease, (110, 70), buttons=Qt.NoButton)
    mouse(o, QEvent.MouseMove, (200, 150), Qt.NoButton, Qt.NoButton)
    mouse(o, QEvent.MouseButtonPress, (200, 150))
    mouse(o, QEvent.MouseButtonRelease, (200, 150), buttons=Qt.NoButton)
    assert o.got == [] and o.scroll == [QRect(10, 10, 100, 60), QRect(100, 80, 300, 200)]


def test_s_switches_to_scrolling(qapp):
    o = make_overlay(mode="area")
    QApplication.sendEvent(o, QKeyEvent(QEvent.KeyPress, Qt.Key_S, Qt.NoModifier, "s"))
    assert o.mode == "scroll" and o.toolbar.buttons["scroll"].isChecked()
    assert "move the mouse to stop" in o.hint()


def test_scrolling_is_only_offered_where_it_works(qapp):
    o = make_overlay(mode="scroll", scrolling=False)
    assert o.mode == "area" and "scroll" not in o.toolbar.buttons
    QApplication.sendEvent(o, QKeyEvent(QEvent.KeyPress, Qt.Key_S, Qt.NoModifier, "s"))
    assert o.mode == "area"


def test_session_runs_the_scrolling_capture_on_the_chosen_area(qapp, monkeypatch):
    from snipforge import capture
    started = []

    class FakeRun:
        def __init__(self, screen, rect, **kwargs):
            started.append((screen, rect))
            from PySide6.QtCore import QObject, Signal

            class Box(QObject):
                finished = Signal(object)
            self._box = Box()
            self.finished = self._box.finished

        def start(self):
            pass

        def stop(self):
            pass

        def cancel(self):
            self.finished.emit(None)

        def blockSignals(self, on):  # noqa: N802 - Qt API
            return self._box.blockSignals(on)

    monkeypatch.setattr(capture, "ScrollCapture", FakeRun)
    monkeypatch.setattr(capture.scroll_input, "available", lambda: True)
    prefs.set_capture_mode("scroll")
    results = []
    session = capture.CaptureSession(results.append)
    session.start()
    overlay = session._overlays[0]
    mouse(overlay, QEvent.MouseButtonPress, (10, 10))
    mouse(overlay, QEvent.MouseMove, (60, 40), Qt.NoButton)
    mouse(overlay, QEvent.MouseButtonRelease, (110, 70), buttons=Qt.NoButton)
    assert not session._overlays and started  # the overlays step aside while it scrolls
    dpr = overlay._dpr
    assert started[0][1] == QRect(round(10 * dpr), round(10 * dpr), round(100 * dpr), round(60 * dpr))
    finished = QImage(10, 10, QImage.Format_RGB32)
    session.scrolling.finished.emit(finished)
    assert results == [finished]


@pytest.mark.desktop
@pytest.mark.skipif(sys.platform != "win32", reason="real Windows scrolling")
def test_real_scrolling_capture_of_a_real_window(qapp):
    from PySide6.QtWidgets import QLabel, QScrollArea

    from snipforge.scrolling import ScrollCapture
    content = text_page(2400).scaled(360, 2400)
    area = QScrollArea()
    label = QLabel()
    label.setPixmap(QPixmap.fromImage(content))
    area.setWidget(label)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    area.resize(360 + area.verticalScrollBar().sizeHint().width(), 400)
    area.show()
    area.raise_()
    area.activateWindow()
    QTest.qWait(400)
    screen = area.screen()
    dpr = screen.devicePixelRatio()
    viewport = area.viewport()
    top_left = viewport.mapToGlobal(QPoint(0, 0)) - screen.geometry().topLeft()
    rect = QRect(round(top_left.x() * dpr), round(top_left.y() * dpr),
                 round(viewport.width() * dpr), round(viewport.height() * dpr))
    try:
        result = run(ScrollCapture(screen, rect), timeout=60)
        assert result is not None
        # the whole content, top to bottom, in one image (the viewport's scrollbar left out or kept)
        assert abs(result.height() - round(2400 * dpr)) <= 2
    finally:
        area.close()





def test_a_frame_grabbed_mid_redraw_is_looked_at_again(qapp):
    full = text_page(1400)
    scroller = FakeScroller(full)
    grabs = []
    real_grab = scroller.grab

    def sometimes_torn():
        frame = real_grab()
        grabs.append(1)
        if len(grabs) == 3:  # half the old view, half the new: lines up with nothing
            torn = frame.copy()
            p = QPainter(torn)
            p.fillRect(0, 0, torn.width(), torn.height() // 2, QColor("#ff00ff"))
            p.end()
            return torn
        return frame
    scroller.grab = sometimes_torn
    result = run(make_capture(scroller))
    assert same(result, full)  # didn't stop at the torn frame


def test_stitcher_says_why_a_frame_added_nothing():
    from snipforge.stitch import Stitcher
    full = page(600)
    s = Stitcher()
    s.add(full.copy(QRect(0, 0, 120, 200)))
    s.add(full.copy(QRect(0, 0, 120, 200)))
    assert s.outcome == "still"
    s.add(full.copy(QRect(0, 60, 120, 200)))
    assert s.outcome == "added"
    s.add(page(600, seed=5).copy(QRect(0, 0, 120, 200)))
    assert s.outcome == "mismatch"
