"""Settings > Capture: the capture toolbar's remembered choices, findable without taking a snip."""
import pytest

from snipforge import autostart, prefs
from snipforge.settings import app_settings

KEYS = ("capture_mode", "capture_delay", "capture_pointer")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    for name in KEYS:
        app_settings().remove(prefs.KEYS[name])
    yield
    for name in KEYS:
        app_settings().remove(prefs.KEYS[name])


@pytest.fixture
def window(qapp, tmp_path):
    from snipforge.settings_window import SettingsWindow
    w = SettingsWindow(library_root=tmp_path)
    yield w
    w.close()


def test_capture_page_shows_the_remembered_choices(window):
    assert window.start_mode.value == "area"
    assert window.delay.value == "0" and not window.include_pointer.isChecked()


def test_choices_made_here_are_what_the_next_snip_uses(window):
    window.start_mode.button("window").click()
    window.delay.button("5").click()
    window.include_pointer.click()
    assert prefs.capture_mode() == "window" and prefs.capture_delay() == 5 and prefs.capture_pointer() is True


def test_choices_made_on_the_toolbar_show_here_next_time(window):
    window.show()
    window.hide()
    prefs.set_capture_mode("screen")
    prefs.set_capture_delay(10)
    prefs.set_capture_pointer(True)
    window.show()
    assert window.start_mode.value == "screen" and window.delay.value == "10" and window.include_pointer.isChecked()


def test_scrolling_is_not_a_start_mode(window):
    assert "scroll" not in window.start_mode._buttons  # it's chosen afresh every time


def test_find_knows_the_new_settings(window):
    from snipforge.settings_window import SECTIONS
    keywords = " ".join(SECTIONS["capture"]).lower()
    assert all(word in keywords for word in ("delay", "pointer", "mode"))


def test_fixed_size_is_a_start_mode(window):
    window.start_mode.button("fixed").click()
    assert prefs.capture_mode() == "fixed"
