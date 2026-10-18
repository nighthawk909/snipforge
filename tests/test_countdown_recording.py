"""snipforge/countdown.py before a recording: the same countdown, in a recording's words."""
from PySide6.QtGui import QGuiApplication

from snipforge.countdown import Countdown


def test_a_recordings_countdown_says_recording(qapp):
    countdown = Countdown(3, QGuiApplication.primaryScreen(), settle_ms=0, recording=True)

    assert countdown.message() == "Recording in 3 seconds"
    assert countdown.now_button.text() == "Record now"
    countdown.tick()
    assert countdown.message() == "Recording in 2 seconds"
    countdown.close()


def test_a_snips_countdown_is_as_it_was(qapp):
    countdown = Countdown(3, QGuiApplication.primaryScreen(), settle_ms=0)

    assert countdown.message() == "Snipping in 3 seconds" and countdown.now_button.text() == "Snip now"
    countdown.close()
