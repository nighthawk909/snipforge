"""The "App name and date" stamp: where a snip came from and when, as text you can edit.

It's filled from the Library entry's metadata: the window title (or, without one, the app's name)
and the time the snip was taken. Private-browsing windows were saved without a title, so their stamp
names only the app; an older snip whose title still says it was private is treated the same way.
"""
from __future__ import annotations

import datetime as dt

from ..capture_area import PRIVATE_MARKERS

SEPARATOR = " · "


def when(created: dt.datetime) -> str:
    """Like "Oct 17, 2026, 3:42 PM"."""
    hour = created.strftime("%I").lstrip("0") or "12"
    return f"{created:%b} {created.day}, {created.year}, {hour}:{created:%M} {created:%p}"


def source(title: str, app: str) -> str:
    """The window title, or the app if there's no title (or it was private browsing)."""
    title, app = " ".join(title.split()), " ".join(app.split())
    if title and not any(marker in title.lower() for marker in PRIVATE_MARKERS):
        return title
    return app


def stamp_text(title: str, app: str, created: dt.datetime) -> str:
    where = source(title, app)
    return f"{where}{SEPARATOR}{when(created)}" if where else when(created)
