"""Private browsing windows never leave their title behind: not in a snip's details, the Library,
its search, or the "App name and date" stamp."""
import pytest
from PySide6.QtCore import QRect

from snipforge import privacy
from snipforge.winrects import WindowInfo


@pytest.mark.parametrize("title, app", [
    ("New tab - [InPrivate] - Microsoft​ Edge", "Edge"),
    ("Bank - Google Chrome (Incognito)", "Chrome"),
    ("Mozilla Firefox Private Browsing", "Firefox"),
    ("Bank (Private) - Brave", "Brave Browser"),
    ("Bank - Opera (Private)", "Opera Internet Browser"),
    ("Bank - Private with Tor", "Firefox"),  # Tor Browser is firefox.exe
    ("Banque - Navigation privée - Mozilla Firefox", "Firefox"),
    ("Bank - Inkognito - Google Chrome", "Chrome"),
    ("Banco - Navegación privada - Firefox", "Firefox"),
    ("Bank - InPrivate - Microsoft Edge", "Edge"),
    ("Bank - In Private - Edge", "Edge"),  # a no-break space
    ("Bank — Mozilla Firefox Private Browsing", "Firefox"),
    ("Банк — Режим инкогнито", "Chrome"),
    ("银行 - 无痕浏览", "Edge"),
])
def test_private_windows_are_recognised(title, app):
    assert privacy.is_private_window(title, app)
    assert privacy.is_private_title(title)  # the stamp's stricter-is-safer check agrees


@pytest.mark.parametrize("title, app", [
    ("Private equity fund.xlsx - Excel", "Excel"),  # an ordinary word in an ordinary app
    ("nighthawk909/private-notes - Visual Studio Code", "VS Code"),
    ("Privacy policy - Word", "Word"),
    ("GitHub - Microsoft Edge", "Edge"),
    ("Pricing.xlsx - Excel", "Excel"),
    ("", "Chrome"),
])
def test_ordinary_windows_keep_their_title(title, app):
    assert not privacy.is_private_window(title, app)


def test_clear_markers_count_in_any_app():
    assert privacy.is_private_window("Report - InPrivate", "Some App")


def test_browsers_are_known_by_name():
    for app in ("Edge", "Chrome", "Firefox", "Brave Browser", "Opera Internet Browser", "Vivaldi", "Arc",
                "Chromium", "Yandex", "Waterfox", "LibreWolf"):
        assert privacy.is_browser(app), app
    for app in ("Excel", "Archive Manager", "ArcGIS Pro", "Teams", ""):
        assert not privacy.is_browser(app), app


def test_a_snip_of_a_private_window_keeps_only_the_app(qapp):
    from snipforge.capture_area import source_for
    window = WindowInfo(QRect(0, 0, 500, 400), "Bank (Private) - Brave", "Brave Browser")
    found = source_for([(QRect(0, 0, 500, 400), window)], QRect(10, 10, 100, 100))
    assert found.app == "Brave Browser" and found.title == ""


def test_the_library_hides_private_titles_saved_before_and_forgets_them(tmp_path, white_image):
    from snipforge.library import Library
    library = Library(tmp_path)
    entry = library.create(white_image)
    library.update_meta(entry, title="Banque - Navigation privée - Mozilla Firefox", app="Firefox")
    assert library.meta(entry).title == "" and library.meta(entry).app == "Firefox"
    library.update_meta(entry, favorite=True)  # the next save of its details drops it from the file too
    assert "privée" not in (entry.path / "meta.json").read_text(encoding="utf-8")


def test_the_library_keeps_ordinary_titles(tmp_path, white_image):
    from snipforge.library import Library
    library = Library(tmp_path)
    entry = library.create(white_image)
    library.update_meta(entry, title="Private equity fund.xlsx - Excel", app="Excel")
    assert library.meta(entry).title == "Private equity fund.xlsx - Excel"
