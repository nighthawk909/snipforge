"""The countdown before a delayed snip: it ticks, can be cancelled or skipped, and is gone before the
screen is frozen."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snipforge.countdown import Countdown


@pytest.fixture
def countdown(qapp):
    c = Countdown(3, QGuiApplication.primaryScreen(), settle_ms=0)
    c.events = []
    c.finished.connect(lambda: c.events.append("finished"))
    c.cancelled.connect(lambda: c.events.append("cancelled"))
    yield c
    c.close()


def test_counts_down_then_hides_before_finishing(countdown):
    countdown.start()
    assert countdown.isVisible() and "3" in countdown.message()
    countdown.tick()
    assert "2" in countdown.message()
    countdown.tick()
    assert countdown.message() == "Snipping in 1 second"
    hidden_when_done = []
    countdown.finished.connect(lambda: hidden_when_done.append(countdown.isVisible()))
    countdown.tick()
    QTest.qWait(10)  # the settle wait before the screen is frozen
    assert countdown.events == ["finished"] and hidden_when_done == [False]


def test_cancel_button_cancels_and_stops_the_clock(countdown):
    countdown.start()
    QTest.mouseClick(countdown.cancel_button, Qt.LeftButton)
    for _ in range(5):
        countdown.tick()
    assert countdown.events == ["cancelled"] and not countdown.isVisible()


def test_snip_now_skips_the_rest(countdown):
    countdown.start()
    countdown.snip_now()
    QTest.qWait(10)
    countdown.tick()
    assert countdown.events == ["finished"]


def test_never_takes_focus_from_the_app_being_snipped(countdown):
    flags = countdown.windowFlags()
    assert flags & Qt.WindowDoesNotAcceptFocus and flags & Qt.WindowStaysOnTopHint
    assert countdown.testAttribute(Qt.WA_ShowWithoutActivating)
    assert countdown.cancel_button.focusPolicy() == Qt.NoFocus


def test_sits_at_the_top_of_its_monitor(countdown):
    countdown.start()
    QApplication.processEvents()
    area = QGuiApplication.primaryScreen().availableGeometry()
    assert area.contains(countdown.geometry())
    assert abs(countdown.geometry().center().x() - area.center().x()) <= 1
    assert countdown.y() - area.y() < 60


def test_rejects_silly_delays(qapp):
    with pytest.raises(ValueError):
        Countdown(0, QGuiApplication.primaryScreen())
    with pytest.raises(ValueError):
        Countdown(61, QGuiApplication.primaryScreen())


def test_screen_readers_hear_each_second(countdown):
    countdown.start()
    countdown.tick()
    assert countdown._label.accessibleName() == "Snipping in 2 seconds"


@pytest.mark.desktop
def test_real_countdown_never_takes_focus_and_stays_out_of_screenshots(qapp):
    import sys
    if sys.platform != "win32":
        pytest.skip("Windows")
    import ctypes

    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QWidget
    backdrop = QWidget(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
    backdrop.setStyleSheet("background: #12ab34;")
    screen = QGuiApplication.primaryScreen()
    area = screen.availableGeometry()
    backdrop.setGeometry(area.x(), area.y(), area.width(), 160)
    backdrop.show()
    QApplication.processEvents()
    QTest.qWait(200)
    foreground = ctypes.windll.user32.GetForegroundWindow()
    c = Countdown(5, screen, settle_ms=0)
    c.start()
    QTest.qWait(300)
    try:
        assert ctypes.windll.user32.GetForegroundWindow() == foreground  # it didn't take focus
        g = c.frameGeometry()
        origin = screen.geometry().topLeft()
        shot = screen.grabWindow(0, g.x() - origin.x(), g.y() - origin.y(), g.width(), g.height()).toImage()
        colors = {shot.pixelColor(x, y).name() for x in range(0, shot.width(), 6)
                  for y in range(0, shot.height(), 6)}
        assert colors == {QColor("#12ab34").name()}  # a screenshot sees what's behind it, not the countdown
    finally:
        c.cancel()
        c.close()
        backdrop.close()
