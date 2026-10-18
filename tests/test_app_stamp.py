"""The "App name and date" stamp: where a snip came from and when, as text you can edit."""
import datetime as dt

import pytest
from PySide6.QtCore import QRectF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snipforge.editor.app_stamp import is_private_title, source, stamp_text, when
from snipforge.editor.edges import Edges
from snipforge.editor.text_items import TextItem
from snipforge.library import Library
from snipforge.winrects import WindowInfo

TAKEN = dt.datetime(2026, 10, 17, 15, 4)


def test_the_text_names_the_window_or_app_and_when():
    assert when(TAKEN) == "Oct 17, 2026, 3:04 PM"
    assert when(dt.datetime(2026, 1, 2, 0, 30)) == "Jan 2, 2026, 12:30 AM"
    assert when(dt.datetime(2026, 12, 31, 12, 5)) == "Dec 31, 2026, 12:05 PM"
    assert stamp_text("Pricing.xlsx - Excel", "Excel", TAKEN) == "Pricing.xlsx - Excel · Oct 17, 2026, 3:04 PM"
    assert stamp_text("", "Edge", TAKEN) == "Edge · Oct 17, 2026, 3:04 PM"  # private browsing: no title kept
    assert stamp_text("", "", TAKEN) == "Oct 17, 2026, 3:04 PM"
    assert stamp_text("  Spaced\nout  ", "", TAKEN).startswith("Spaced out · ")


def test_the_date_is_english_whatever_the_locale(monkeypatch):
    import locale
    try:
        monkeypatch.setattr(locale, "setlocale", locale.setlocale)
        locale.setlocale(locale.LC_TIME, "de_DE.UTF-8" if hasattr(locale, "LC_MESSAGES") else "German")
    except locale.Error:
        pytest.skip("no German locale here")
    try:
        assert when(TAKEN) == "Oct 17, 2026, 3:04 PM"
    finally:
        locale.setlocale(locale.LC_TIME, "C")


@pytest.mark.parametrize("title", [
    "Bank - Google Chrome (Incognito)", "Bank — Mozilla Firefox Private Browsing",
    "Bank - [InPrivate] - Microsoft Edge",
    "Bank - Brave (Private)", "Bank - Opera (Private)", "Bank - Private with Tor", "Bank - Private Window",
    "Banco — Navegación privada", "Banque — Navigation privée", "Bank — Privates Surfen", "Bank - İncognito",
    "Bank - Inkognito", "Pay\u00a0Private\u00a0Browsing", "Private\u200bBrowsing", "INPRIVATE",
])
def test_private_windows_never_name_the_page(title):
    assert is_private_title(title)
    assert source(title, "Browser") == "Browser"


def test_text_that_reorders_or_hides_is_removed():
    assert source("Report\u202e evil\u200f\x07 here", "") == "Report evil here"
    assert "\u202e" not in stamp_text("x\u202ey", "", TAKEN)


@pytest.fixture
def make_editor(qapp, tmp_path):
    from PySide6.QtGui import QColor, QImage

    from snipforge.editor.window import EditorWindow
    lib = Library(tmp_path)
    made = []

    def build(source=None, entry=True, size=(1200, 400)):
        image = QImage(*size, QImage.Format_ARGB32)
        image.fill(QColor("white"))
        e = lib.create(image, source=source) if entry else None
        w = EditorWindow(image, lib, e)
        w.resize(1100, 700)
        w.show()
        QApplication.processEvents()
        made.append(w)
        return w
    yield build
    for w in made:
        w.close()


def stamps(canvas):
    return [i for i in canvas.annotations() if isinstance(i, TextItem)]


def click_stamp(editor):
    editor.canvas.set_tool("stamp")
    QTest.mouseClick(editor.panel.source_stamp_button(), Qt.LeftButton)
    return stamps(editor.canvas)[-1]


def test_the_button_adds_an_editable_stamp_inside_the_snip(make_editor):
    editor = make_editor(WindowInfo(None, "Quarterly report - Word", "Word"))
    assert editor.panel.source_stamp_button().isEnabled()
    stamp = click_stamp(editor)
    assert stamp.toPlainText() == stamp_text("Quarterly report - Word", "Word", editor._entry.created)
    assert QRectF(0, 0, 1200, 400).contains(stamp.sceneBoundingRect())
    assert stamp.sceneBoundingRect().bottom() > 300  # the bottom-left corner
    assert editor.canvas.selected_annotations() == [stamp]
    assert "where and when" in editor.statusBar().currentMessage()
    editor.canvas.undo_stack.undo()
    assert stamps(editor.canvas) == []
    editor.canvas.undo_stack.redo()
    assert stamps(editor.canvas) == [stamp]


def test_private_browsing_stays_private(make_editor):
    editor = make_editor(WindowInfo(None, "", "Edge"))  # capture already blanked the title
    assert click_stamp(editor).toPlainText().startswith("Edge · ")


def test_no_library_entry_no_stamp(make_editor):
    editor = make_editor(entry=False)
    editor.canvas.set_tool("stamp")
    button = editor.panel.source_stamp_button()
    assert not button.isEnabled() and "library" in button.toolTip().lower() and button.accessibleDescription()


def test_a_long_title_is_shortened_never_the_date(make_editor):
    title = "A very long page title " * 8 + "- Some Browser"
    for size in ((500, 300), (300, 200)):
        editor = make_editor(WindowInfo(None, title, "Browser"), size=size)
        stamp = click_stamp(editor)
        text = stamp.toPlainText()
        assert text.endswith(when(editor._entry.created))
        assert stamp.sceneBoundingRect().right() <= size[0] and stamp.sceneBoundingRect().left() >= 0, size


def test_the_stamp_sits_inside_a_crop_and_clear_of_edges(make_editor):
    editor = make_editor(WindowInfo(None, "Doc - Word", "Word"))
    canvas = editor.canvas
    canvas.set_crop_rect(QRectF(300, 100, 600, 250))
    canvas.set_edges(Edges(border=12, torn=frozenset({"bottom"})))
    stamp = click_stamp(editor)
    r = stamp.sceneBoundingRect()
    assert r.left() >= 300 + 12 and r.bottom() <= 350 - 12 - 9 and r.right() <= 900


def test_a_big_snip_gets_bigger_text(make_editor):
    editor = make_editor(WindowInfo(None, "Doc - Word", "Word"), size=(2400, 600))
    assert click_stamp(editor).style.font_size == 24
