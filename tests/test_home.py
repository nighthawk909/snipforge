"""Library window: cards grouped by day, search, favorites, tags, filters and actions."""
import time

import pytest
from PySide6.QtCore import QEvent, QObject, QPointF, Qt, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QStyleOptionViewItem

from snipforge.home import HomeWindow, snip_hint
from snipforge.library import DOCUMENT, Library
from snipforge.library_view import ROW_ROLE
from snipforge.winrects import WindowInfo


@pytest.fixture
def lib(tmp_path, white_image):
    lib = Library(tmp_path)
    lib.create(white_image, source=WindowInfo(None, "Margin review.xlsx - Excel", "Excel"))
    time.sleep(0.01)
    outlook = lib.create(white_image, source=WindowInfo(None, "Inbox - Outlook", "Outlook"))
    lib.update_meta(outlook, text="invoice approval needed", tags=["finance"])
    time.sleep(0.01)
    edge = lib.create(white_image, source=WindowInfo(None, "Sprint board", "Edge"))
    (edge.path / DOCUMENT).write_text("{}", encoding="utf-8")
    return lib


@pytest.fixture
def home(qapp, lib):
    opened = []
    window = HomeWindow(lib, lambda: None, opened.append)
    window.opened = opened
    window.resize(1100, 700)
    window.refresh()
    yield window
    window.close()


def search(home, text):
    home._search.setText(text)
    home._search_timer.stop()
    home.apply()  # skip the typing delay


def titles(home):
    model = home.grid.model_
    rows = [model.data(model.index(n), ROW_ROLE) for n in range(model.rowCount())]
    return [r.item.meta.title for r in rows if not r.is_header]


def card_index(home, title):
    model = home.grid.model_
    for n in range(model.rowCount()):
        row = model.data(model.index(n), ROW_ROLE)
        if not row.is_header and row.item.meta.title == title:
            return model.index(n)
    raise AssertionError(title)


def test_lists_every_snip_newest_first_under_a_date_header(home):
    model = home.grid.model_
    first = model.data(model.index(0), ROW_ROLE)
    assert first.is_header and first.label == "Today"
    assert titles(home) == ["Sprint board", "Inbox - Outlook", "Margin review.xlsx - Excel"]
    assert home._count.text() == "3 snips"
    assert model.flags(model.index(0)) & Qt.ItemIsSelectable == Qt.NoItemFlags  # headers can't be selected


def test_search_matches_recognized_words_titles_and_apps(home):
    search(home, "invoice")
    assert titles(home) == ["Inbox - Outlook"]
    search(home, "excel margin")
    assert titles(home) == ["Margin review.xlsx - Excel"]
    search(home, "nothing like this")
    assert titles(home) == [] and home._stack.currentIndex() == 1
    assert "nothing like this" in home._empty_title.text()


def test_views_tags_and_app_chips(home):
    home.show_view("edited")
    assert titles(home) == ["Sprint board"] and home._title.text() == "Recently edited"
    home.show_tag("finance")
    assert titles(home) == ["Inbox - Outlook"] and home._title.text() == "#finance"
    home.show_view("all")
    home._set_app("Excel")
    assert titles(home) == ["Margin review.xlsx - Excel"]
    home._set_app(None)
    home._set_period("today")
    assert len(titles(home)) == 3


def test_clicking_the_star_makes_a_favorite(home, lib):
    home.show()
    QApplication.processEvents()
    index = card_index(home, "Inbox - Outlook")
    rect = home.grid.visualRect(index)
    star = home.grid.delegate.star_rect(rect).center()
    option = QStyleOptionViewItem()
    option.rect = rect
    event = QMouseEvent(QEvent.MouseButtonRelease, QPointF(star), QPointF(star), Qt.LeftButton, Qt.LeftButton,
                        Qt.NoModifier)
    assert home.grid.delegate.editorEvent(event, home.grid.model_, option, index)
    entry = next(e for e in lib.entries() if lib.meta(e).app == "Outlook")
    assert lib.meta(entry).favorite
    home.show_view("favorites")
    assert titles(home) == ["Inbox - Outlook"]
    assert home._nav["favorites"]._count == "1"


def test_open_copy_and_trash(home, lib, monkeypatch, no_dialogs):
    index = card_index(home, "Sprint board")
    home.grid.setCurrentIndex(index)
    home.grid.activated.emit(index)
    entry_id = home.grid.current_id()
    assert home.opened == [entry_id]
    home._copy(entry_id)
    assert not QGuiApplication.clipboard().image().isNull()
    monkeypatch.setattr(lib, "move_to_trash", lambda entry: False)  # never touch the real Recycle Bin
    home._trash([entry_id])
    assert no_dialogs and no_dialogs[-1][0] == "warning"


def test_tags_can_be_edited(home, lib, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    entry = next(e for e in lib.entries() if lib.meta(e).app == "Edge")
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Bugs, q3-review", True))
    home.edit_tags(entry.id)
    assert lib.meta(entry).tags == ("bugs", "q3-review")
    assert [home._tags_box.itemAt(n).widget().text() for n in range(home._tags_box.count())] == \
        ["bugs", "finance", "q3-review"]


def test_empty_library_explains_how_to_snip(qapp, tmp_path):
    home = HomeWindow(Library(tmp_path), lambda: None, lambda _i: None)
    home.refresh()
    # Win+Shift+S on Windows; the menu bar icon on a Mac (tests/test_platform_guards.py)
    assert home._stack.currentIndex() == 1 and home._empty_hint.text() == snip_hint()
    assert not home._empty_button.isHidden()


def test_newly_recognized_words_become_searchable(home, lib):
    class FakeIndexer(QObject):
        indexed = Signal(str)

        def backfill(self, entries):
            self.backfilled = len(entries)  # only snips not read yet

    indexer = FakeIndexer()
    window = HomeWindow(lib, lambda: None, lambda _i: None, indexer=indexer)
    window.show()
    assert indexer.backfilled == 3
    search(window, "gross margin")
    assert titles(window) == []
    entry = next(e for e in lib.entries() if lib.meta(e).app == "Excel")
    lib.update_meta(entry, text="Gross margin by month", indexed=True)
    indexer.indexed.emit(entry.id)
    assert window._indexed_timer.isActive()  # regrouping waits a moment so results don't jump around
    window._indexed_timer.stop()
    window.apply()
    assert titles(window) == ["Margin review.xlsx - Excel"]
    window.close()


def test_starring_keeps_the_selection_and_updates_just_that_snip(home, lib, monkeypatch):
    index = card_index(home, "Inbox - Outlook")
    home.grid.setCurrentIndex(index)
    entry_id = home.grid.current_id()
    scans = []
    monkeypatch.setattr(lib, "scan", lambda: scans.append(1) or [])
    home._favorite_current()
    assert scans == []  # no full re-read of the library
    assert home.grid.current_id() == entry_id and home.grid.selected_ids() == [entry_id]
    assert home._nav["favorites"]._count == "1"


def test_right_click_on_empty_space_does_nothing(home, monkeypatch):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QMenu
    shown = []
    monkeypatch.setattr(QMenu, "exec", lambda *a: shown.append(True))
    home.grid.setCurrentIndex(card_index(home, "Sprint board"))
    home._context_menu(QPoint(5000, 5000))
    assert shown == [] and home.grid.selected_ids() == []


def test_moving_many_snips_asks_first(home, lib, monkeypatch, no_dialogs):
    from snipforge import home as home_module
    monkeypatch.setattr(home_module, "CONFIRM_TRASH_OVER", 1)
    moved = []
    monkeypatch.setattr(lib, "move_to_trash", lambda entry: moved.append(entry.id) or True)
    ids = [e.id for e in lib.entries()]
    home._trash(ids)
    assert no_dialogs[-1][0] == "question" and moved == []  # the answer was No
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    home._trash(ids)
    assert sorted(moved) == sorted(ids) and titles(home) == []


def test_a_briefly_locked_meta_file_keeps_what_was_shown(home, lib, monkeypatch):
    entry = next(e for e in lib.entries() if lib.meta(e).app == "Outlook")
    lib.update_meta(entry, favorite=True)

    def locked(_entry):
        raise PermissionError("in use")

    monkeypatch.setattr(lib, "read_meta", locked)
    home.update_entry(entry.id)
    item = next(i for i in home._items if i.entry.id == entry.id)
    assert item.meta.app == "Outlook" and item.meta.tags == ("finance",)  # not wiped to defaults
