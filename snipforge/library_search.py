"""Library search and date grouping (pure functions, so they're easy to test)."""
from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from .library import Entry, EntryMeta


def matches(meta: EntryMeta, query: str) -> bool:
    """Every word of the query appears in the title, app, recognized text or tags."""
    words = query.lower().split()
    if not words:
        return True
    haystack = " ".join((meta.title, meta.app, meta.text, " ".join(meta.tags))).lower()
    return all(word in haystack for word in words)


def date_label(when: dt.datetime, now: dt.datetime) -> str:
    day, today = when.date(), now.date()
    if day == today:
        return "Today"
    if day == today - dt.timedelta(days=1):
        return "Yesterday"
    if day.isocalendar()[:2] == today.isocalendar()[:2] and day < today:
        return "Earlier this week"
    if (day.year, day.month) == (today.year, today.month):
        return "Earlier this month"
    return when.strftime("%B %Y")


def group_by_date(created: Sequence[dt.datetime], now: dt.datetime) -> list[tuple[str, list[int]]]:
    """Indexes of `created` (newest first) grouped under labels like 'Today' or 'September 2026'."""
    groups: list[tuple[str, list[int]]] = []
    for index, when in enumerate(created):
        label = date_label(when, now)
        if groups and groups[-1][0] == label:
            groups[-1][1].append(index)
        else:
            groups.append((label, [index]))
    return groups


@dataclass(frozen=True)
class Item:
    entry: Entry
    created: dt.datetime
    meta: EntryMeta
    edited_at: float | None = None  # when its markup was last saved (None: never edited)


VIEWS = ("all", "favorites", "edited")
PERIODS = ("any", "today", "week", "month")


def in_period(created: dt.datetime, period: str, now: dt.datetime) -> bool:
    day, today = created.date(), now.date()
    if period == "today":
        return day == today
    if period == "week":
        return day.isocalendar()[:2] == today.isocalendar()[:2]
    if period == "month":
        return (day.year, day.month) == (today.year, today.month)
    return True


def select(items: Sequence[Item], *, view: str = "all", tag: str | None = None, period: str = "any",
           app: str | None = None, query: str = "", now: dt.datetime) -> list[Item]:
    """The snips to show, newest first ('edited' sorts by when the markup was last saved)."""
    chosen = [
        i for i in items
        if (view != "favorites" or i.meta.favorite)
        and (view != "edited" or i.edited_at is not None)
        and (tag is None or tag in i.meta.tags)
        and (app is None or i.meta.app == app)
        and in_period(i.created, period, now)
        and matches(i.meta, query)
    ]
    if view == "edited":
        chosen.sort(key=lambda i: i.edited_at or 0.0, reverse=True)
    return chosen


def top_apps(items: Sequence[Item], limit: int = 5) -> list[str]:
    counts = Counter(i.meta.app for i in items if i.meta.app)
    return [app for app, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def tag_counts(items: Sequence[Item]) -> list[tuple[str, int]]:
    counts = Counter(tag for i in items for tag in i.meta.tags)
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
