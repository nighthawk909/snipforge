"""Snip metadata (source app, favorites, tags, recognized text), search and date grouping."""
import datetime as dt
import json

import pytest

from snipforge.library import EntryMeta, Library
from snipforge.library_search import group_by_date, matches
from snipforge.winrects import WindowInfo, friendly_app_name


def test_new_snip_records_where_it_came_from(tmp_path, white_image):
    lib = Library(tmp_path)
    entry = lib.create(white_image, source=WindowInfo(None, "Margin review.xlsx - Excel", "Excel"))
    meta = lib.meta(entry)
    assert meta.title == "Margin review.xlsx - Excel" and meta.app == "Excel"
    assert not meta.favorite and meta.tags == () and meta.text == ""
    plain = lib.create(white_image)
    assert lib.meta(plain) == EntryMeta()


def test_meta_updates_are_saved_and_validated(tmp_path, white_image):
    lib = Library(tmp_path)
    entry = lib.create(white_image)
    lib.update_meta(entry, favorite=True, tags=[" Bugs ", "bugs", "q3-review", "", "x" * 80])
    meta = lib.meta(entry)
    assert meta.favorite
    assert meta.tags == ("bugs", "q3-review", "x" * 32)
    lib.update_meta(entry, text="word " * 10_000)
    assert len(lib.meta(entry).text) <= 20_000
    with pytest.raises(ValueError):
        lib.update_meta(entry, colour="red")


def test_damaged_meta_falls_back_to_defaults(tmp_path, white_image):
    lib = Library(tmp_path)
    entry = lib.create(white_image)
    (entry.path / "meta.json").write_text("{broken", encoding="utf-8")
    assert lib.meta(entry) == EntryMeta()
    (entry.path / "meta.json").write_text(json.dumps({"favorite": "yes", "tags": "nope", "title": 5}),
                                          encoding="utf-8")
    assert lib.meta(entry) == EntryMeta()
    lib.update_meta(entry, favorite=True)  # repairs it
    assert lib.meta(entry).favorite


@pytest.mark.parametrize("exe, name", [
    ("C:/Program Files/Microsoft Office/root/Office16/EXCEL.EXE", "Excel"),
    ("msedge.exe", "Edge"), ("ms-teams.exe", "Teams"), ("PBIDesktop.exe", "Power BI"),
    ("C:/tools/my_tool.exe", "My Tool"), ("", ""),
])
def test_friendly_app_names(exe, name):
    assert friendly_app_name(exe) == name


def test_search_matches_every_word_across_title_app_text_and_tags():
    meta = EntryMeta(title="Margin review.xlsx", app="Excel", text="Gross margin by month FY26",
                     tags=("q3-review",))
    assert matches(meta, "")
    assert matches(meta, "excel margin")
    assert matches(meta, "GROSS fy26")
    assert matches(meta, "q3")
    assert not matches(meta, "margin outlook")


def test_group_by_date_labels():
    now = dt.datetime(2026, 10, 16, 15, 0)  # a Friday
    created = [now, now - dt.timedelta(days=1), now - dt.timedelta(days=3), now - dt.timedelta(days=9),
               dt.datetime(2026, 9, 2), dt.datetime(2025, 12, 24)]
    groups = group_by_date(created, now)
    assert [label for label, _ in groups] == [
        "Today", "Yesterday", "Earlier this week", "Earlier this month", "September 2026", "December 2025"]
    assert [items for _, items in groups] == [[0], [1], [2], [3], [4], [5]]


def make_items():
    from snipforge.library import Entry
    from snipforge.library_search import Item
    now = dt.datetime(2026, 10, 16, 15, 0)
    rows = [
        ("a", now, EntryMeta(title="Margin review.xlsx - Excel", app="Excel", favorite=True, tags=("q3",)), 100.0),
        ("b", now - dt.timedelta(hours=3), EntryMeta(title="Inbox - Outlook", app="Outlook"), None),
        ("c", now - dt.timedelta(days=2), EntryMeta(title="Sprint board", app="Edge", tags=("q3", "bugs")), 300.0),
        ("d", now - dt.timedelta(days=40), EntryMeta(title="Old one", app="Excel"), None),
    ]
    return now, [Item(Entry(i, None), created, meta, edited) for i, created, meta, edited in rows]


def ids(items):
    return [i.entry.id for i in items]


def test_select_by_view_tag_period_app_and_query():
    from snipforge.library_search import select
    now, items = make_items()
    assert ids(select(items, now=now)) == ["a", "b", "c", "d"]
    assert ids(select(items, view="favorites", now=now)) == ["a"]
    assert ids(select(items, view="edited", now=now)) == ["c", "a"]  # most recently edited first
    assert ids(select(items, tag="q3", now=now)) == ["a", "c"]
    assert ids(select(items, period="today", now=now)) == ["a", "b"]
    assert ids(select(items, period="week", now=now)) == ["a", "b", "c"]
    assert ids(select(items, period="month", now=now)) == ["a", "b", "c"]
    assert ids(select(items, app="Excel", now=now)) == ["a", "d"]
    assert ids(select(items, query="sprint q3", now=now)) == ["c"]
    assert ids(select(items, view="favorites", query="outlook", now=now)) == []


def test_top_apps_and_tags_are_counted():
    from snipforge.library_search import tag_counts, top_apps
    _now, items = make_items()
    assert top_apps(items, limit=2) == ["Excel", "Edge"]  # by count, then name
    assert tag_counts(items) == [("q3", 2), ("bugs", 1)]


def test_saves_retry_when_windows_briefly_locks_the_file(tmp_path, white_image, monkeypatch):
    import os

    from snipforge import library as library_module
    lib = Library(tmp_path)
    entry = lib.create(white_image)
    real, calls = os.replace, []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) < 3:
            raise PermissionError("in use by another process")
        real(src, dst)

    monkeypatch.setattr(library_module.os, "replace", flaky)
    lib.update_meta(entry, favorite=True)
    assert len(calls) == 3 and lib.meta(entry).favorite


def test_a_locked_meta_file_is_never_overwritten_with_defaults(tmp_path, white_image, monkeypatch):
    from pathlib import Path
    lib = Library(tmp_path)
    entry = lib.create(white_image)
    lib.update_meta(entry, favorite=True, tags=["keep"])
    real = Path.read_bytes

    def locked(self):
        if self.name == "meta.json":
            raise PermissionError("in use by another process")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", locked)
    assert lib.meta(entry) == EntryMeta()  # reading for display is forgiving
    with pytest.raises(PermissionError):
        lib.update_meta(entry, indexed=True)  # but an update must not clobber what it couldn't read
    monkeypatch.setattr(Path, "read_bytes", real)
    assert lib.meta(entry).favorite and lib.meta(entry).tags == ("keep",)


def test_a_damaged_meta_file_is_kept_aside(tmp_path, white_image):
    lib = Library(tmp_path)
    entry = lib.create(white_image)
    (entry.path / "meta.json").write_text("{broken", encoding="utf-8")
    lib.update_meta(entry, favorite=True)
    assert (entry.path / "meta.json.bad").read_text(encoding="utf-8") == "{broken"
    assert lib.meta(entry).favorite


def test_odd_text_is_cleaned_before_saving(tmp_path, white_image):
    lib = Library(tmp_path)
    entry = lib.create(white_image, source=WindowInfo(None, "Project \ud83d notes", "App"))
    assert lib.meta(entry).title == "Project � notes"


def test_new_snips_have_a_small_preview_and_scan_reports_files(tmp_path):
    from PySide6.QtGui import QColor, QImage
    big = QImage(2400, 1200, QImage.Format_ARGB32)
    big.fill(QColor("white"))
    lib = Library(tmp_path)
    entry = lib.create(big)
    preview = QImage(str(entry.path / "preview.png"))
    assert max(preview.width(), preview.height()) <= 480
    (entry.path / "document.json").write_text("{}", encoding="utf-8")
    lib.update_meta(entry, favorite=True)
    [snap] = lib.scan()
    assert snap.entry.id == entry.id and snap.has_preview and snap.edited_at and snap.meta_stamp


@pytest.mark.parametrize("today, first_weekday, expected", [
    (dt.date(2026, 10, 19), 1, dt.date(2026, 10, 19)),  # Monday, weeks start Monday
    (dt.date(2026, 10, 18), 1, dt.date(2026, 10, 12)),  # Sunday, weeks start Monday
    (dt.date(2026, 10, 19), 7, dt.date(2026, 10, 18)),  # Monday, weeks start Sunday (US)
    (dt.date(2027, 1, 1), 7, dt.date(2026, 12, 27)),    # across a year boundary
])
def test_week_start_follows_the_locale(today, first_weekday, expected):
    from snipforge.library_search import week_start
    assert week_start(today, first_weekday) == expected


def test_sunday_counts_as_this_week_for_us_users_on_monday():
    from snipforge.library_search import date_label, in_period
    monday = dt.datetime(2026, 10, 19, 9, 0)
    sunday = dt.datetime(2026, 10, 18, 9, 0)
    assert date_label(sunday, monday, 7) == "Yesterday" and in_period(sunday, "week", monday, 7)
    assert not in_period(sunday, "week", monday, 1)  # weeks start Monday elsewhere
    future = dt.datetime(2026, 10, 25)
    assert date_label(future, monday) == "Today" and in_period(future, "today", monday)


def test_hash_tags_match():
    meta = EntryMeta(title="Sprint board", tags=("bugs",))
    assert matches(meta, "#bugs") and not matches(meta, "#q3")
