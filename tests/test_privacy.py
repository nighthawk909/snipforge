"""Private browsing windows don't leave their title behind: not in a snip's details, the Library,
its search, or the "App name and date" stamp (best effort, from the window's title and app)."""
import json
import threading
import time

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

from snipforge import prefs, privacy
from snipforge.settings import app_settings
from snipforge.winrects import WindowInfo


@pytest.mark.parametrize("title, app", [
    ("New tab - [InPrivate] - Microsoft​ Edge", "Edge"),
    ("Bank - Google Chrome (Incognito)", "Chrome"),
    ("Bank — Mozilla Firefox Private Browsing", "Firefox"),  # a no-break space in the marker
    ("Bank - Brave (Private)", "Brave Browser"),
    ("Bank - Opera (Private)", "Opera Internet Browser"),
    ("Bank — Tor Browser", "Firefox"),  # Tor Browser is firefox.exe, and always private
    ("Banque — Navigation privée de Mozilla Firefox", "Firefox"),
    ("Bank - Google Chrome (Inkognito)", "Chrome"),
    ("Banco — Navegación privada de Mozilla Firefox", "Firefox"),
    ("Banco - Google Chrome (Anônimo)", "Chrome"),
    ("Banca — Navigazione anonima di Mozilla Firefox", "Firefox"),
    ("Bank — Tryb prywatny Mozilla Firefox", "Firefox"),
    ("銀行 - Google Chrome (無痕)", "Chrome"),  # Traditional Chinese
    ("银行 - Google Chrome (无痕)", "Chrome"),  # Simplified Chinese
    ("은행 - Google Chrome (시크릿)", "Chrome"),  # Korean
    ("銀行 — Mozilla Firefox プライベートブラウジング",
     "Firefox"),  # Japanese
    ("Банк - Google Chrome (инкогнито)", "Chrome"),
    ("Banque — Navigation privée de Mozilla Firefox", ""),  # no app name: the title says browser
])
def test_private_windows_are_recognised(title, app):
    assert privacy.is_private_window(title, app)
    assert privacy.is_private_title(title)  # the stamp's check never says less


@pytest.mark.parametrize("title, app", [
    ("Private equity fund.xlsx - Excel", "Excel"),  # an ordinary word in an ordinary app
    ("nighthawk909/private-notes - Visual Studio Code", "VS Code"),
    ("Privacy policy - Word", "Word"),
    ("private_clients.db - DB Browser for SQLite", "DB Browser for SQLite"),
    ("GitHub - Microsoft​ Edge", "Edge"),
    ("Private Link Center - Microsoft Azure - Microsoft​ Edge", "Edge"),  # the page, not the window
    ("Private equity - Wikipedia - Google Chrome", "Chrome"),
    ("Privatization - Wikipedia — Mozilla Firefox", "Firefox"),
    ("Cuentas privadas - Banco - Google Chrome", "Chrome"),
    ("Budget (private) - Google Sheets - Google Chrome", "Chrome"),  # brackets in the page's own title
    ("Q3 forecast [Private] - SharePoint - Microsoft​ Edge", "Edge"),
    ("Private Link Center | Microsoft Azure", "Edge"),  # a web app window: no browser part
    ("Private Edge Cases", "File Explorer"),
    ("Tor relay private keys", "Windows Terminal"),
    ("", "Chrome"),
])
def test_ordinary_windows_keep_their_title(title, app):
    assert not privacy.is_private_window(title, app)


def test_clear_markers_count_in_any_app():
    assert privacy.is_private_window("Report - InPrivate", "Some App")


def test_the_stamp_is_more_careful_than_the_library():
    """A stamp ends up in a shared image: there, any hint anywhere in the title is enough."""
    title = "Private equity - Wikipedia - Google Chrome"
    assert privacy.is_private_title(title) and not privacy.is_private_window(title, "Chrome")


def test_every_hint_can_match_a_title_written_like_it():
    for hint in privacy.HINTS:
        assert privacy.folded(hint) == hint  # folded like the titles, so it can be found in them


def test_browsers_are_known_by_name():
    for app in ("Edge", "Chrome", "Firefox", "Brave Browser", "Opera Internet Browser", "Vivaldi", "Arc",
                "Chromium", "Yandex", "Waterfox", "LibreWolf", "Pale Moon", "Internet Explorer",
                "QQ浏览器", "Avast Secure Browser", "Epic Privacy Browser", "Samsung Internet"):
        assert privacy.is_browser(app), app
    for app in ("Excel", "Archive Manager", "ArcGIS Pro", "Teams", "File Explorer", "DB Browser for SQLite", ""):
        assert not privacy.is_browser(app), app


# --- where titles are kept ------------------------------------------------------------------------

def test_a_snip_of_a_private_window_keeps_only_the_app(qapp):
    from snipforge.capture_area import source_for
    window = WindowInfo(QRect(0, 0, 500, 400), "Bank - Brave (Private)", "Brave Browser")
    found = source_for([(QRect(0, 0, 500, 400), window)], QRect(10, 10, 100, 100))
    assert found.app == "Brave Browser" and found.title == ""


def _saved_by_an_older_version(entry, title, app):
    (entry.path / "meta.json").write_text(json.dumps({"title": title, "app": app}), encoding="utf-8")


def test_a_private_title_is_never_written(tmp_path, white_image):
    from snipforge.library import Library
    library = Library(tmp_path)
    entry = library.create(white_image)
    library.update_meta(entry, title="Bank - Google Chrome (Incognito)", app="Chrome")
    assert "Bank" not in (entry.path / "meta.json").read_text(encoding="utf-8")


def test_titles_older_versions_saved_are_hidden_then_cleared(tmp_path, white_image):
    from snipforge.library import Library
    library = Library(tmp_path)
    private, ordinary = library.create(white_image), library.create(white_image)
    _saved_by_an_older_version(private, "Banque — Navigation privée de Mozilla Firefox", "Firefox")
    _saved_by_an_older_version(ordinary, "Private equity fund.xlsx - Excel", "Excel")
    assert library.meta(private).title == "" and library.meta(private).app == "Firefox"
    assert library.forget_private_titles() == (1, True)
    assert "Banque" not in (private.path / "meta.json").read_text(encoding="utf-8")
    assert library.meta(ordinary).title == "Private equity fund.xlsx - Excel"
    assert library.forget_private_titles() == (0, True)


def test_a_snip_that_cant_be_read_or_written_now_is_tried_again(tmp_path, white_image, monkeypatch):
    from snipforge.library import Library
    library = Library(tmp_path)
    locked, private = library.create(white_image), library.create(white_image)
    (locked.path / "meta.json").mkdir()  # can't be read, like a file sync has locked
    _saved_by_an_older_version(private, "Bank - Google Chrome (Inkognito)", "Chrome")
    assert library.forget_private_titles() == (1, False)

    def busy(entry, **_changes):
        raise PermissionError("locked")

    _saved_by_an_older_version(private, "Bank - Google Chrome (Inkognito)", "Chrome")
    monkeypatch.setattr(library, "update_meta", busy)
    (locked.path / "meta.json").rmdir()
    assert library.forget_private_titles() == (0, False)


def test_a_sweep_stops_when_asked(tmp_path, white_image):
    from snipforge.library import Library
    library = Library(tmp_path)
    entry = library.create(white_image)
    _saved_by_an_older_version(entry, "Bank - Google Chrome (Inkognito)", "Chrome")
    assert library.forget_private_titles(stop=lambda: True) == (0, False)
    assert "Bank" in (entry.path / "meta.json").read_text(encoding="utf-8")


@pytest.fixture
def clean_sweep_pref():
    app_settings().remove(prefs.KEYS["private_titles_checked"])
    yield
    app_settings().remove(prefs.KEYS["private_titles_checked"])


def _forge(qapp, library, monkeypatch):
    from snipforge import app as app_module
    monkeypatch.setattr(app_module.HotkeyListener, "start", lambda self: True)
    monkeypatch.setattr(app_module.HotkeyListener, "stop", lambda self: None)
    monkeypatch.setattr(app_module.UpdateFlow, "start", lambda self: None)
    return app_module.SnipForgeApp(qapp, library)


def test_the_app_clears_old_private_titles_once_in_the_background(qapp, tmp_path, white_image, monkeypatch,
                                                                   no_dialogs, clean_sweep_pref):
    from snipforge.library import Library
    library = Library(tmp_path)
    entry = library.create(white_image)
    _saved_by_an_older_version(entry, "Bank - Google Chrome (Inkognito)", "Chrome")
    forge = _forge(qapp, library, monkeypatch)
    try:
        deadline = time.time() + 5
        while prefs.private_titles_checked() < privacy.CHECK_VERSION and time.time() < deadline:
            QApplication.processEvents()
            time.sleep(0.02)
        assert prefs.private_titles_checked() == privacy.CHECK_VERSION
        assert "Bank" not in (entry.path / "meta.json").read_text(encoding="utf-8")
    finally:
        forge.indexer.close()
        forge._shortcuts.close()


def test_the_app_tries_again_next_start_if_a_snip_was_locked(qapp, tmp_path, monkeypatch, no_dialogs,
                                                            clean_sweep_pref):
    from snipforge.library import Library
    library = Library(tmp_path)
    swept = threading.Event()
    monkeypatch.setattr(library, "forget_private_titles", lambda stop: swept.set() or (0, False))
    forge = _forge(qapp, library, monkeypatch)
    try:
        assert swept.wait(5)
        for _ in range(20):
            QApplication.processEvents()
            time.sleep(0.01)
        assert prefs.private_titles_checked() == 0
    finally:
        forge.indexer.close()
        forge._shortcuts.close()


def test_the_app_does_not_sweep_again_once_done(qapp, tmp_path, monkeypatch, no_dialogs, clean_sweep_pref):
    from snipforge.library import Library
    prefs.set_private_titles_checked(privacy.CHECK_VERSION)
    library = Library(tmp_path)
    swept = threading.Event()
    monkeypatch.setattr(library, "forget_private_titles", lambda stop: swept.set() or (0, True))
    forge = _forge(qapp, library, monkeypatch)
    try:
        time.sleep(0.1)
        assert not swept.is_set()
    finally:
        forge.indexer.close()
        forge._shortcuts.close()
