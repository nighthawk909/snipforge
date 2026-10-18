"""The "App name and date" stamp: where a snip came from and when, as text you can edit.

It's filled from the Library entry's metadata: the window title (or, without one, the app's name)
and the time the snip was taken. Private-browsing windows were saved without a title, so their stamp
names only the app. A title that still looks private (an older snip, a browser or language the
capture check doesn't know) is treated the same way: here, saying too little is the safe mistake.
"""
from __future__ import annotations

import datetime as dt
import unicodedata

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetricsF

from ..capture_area import PRIVATE_MARKERS

SEPARATOR = " · "
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
# Folded (lower case, no accents) words that mark a private window in some browser or language.
PRIVATE_HINTS = (
    *PRIVATE_MARKERS, "privat", "privad", "prive", "incognit", "inkognit", "gizli",
    "シークレット",  # Japanese: secret (Chrome's incognito)
    "无痕", "隐私", "隱私",  # Chinese: traceless, private
    "시크릿",  # Korean: secret
    "приват", "инкогнито",  # Russian
)
# Characters that reorder or hide text (bidi controls), which a page title could use to garble the date.
HIDDEN = {"\u061c", "\u200e", "\u200f", "\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2066", "\u2067", "\u2068",
          "\u2069"}


def when(created: dt.datetime) -> str:
    """Like "Oct 17, 2026, 3:42 PM", in English whatever the system locale."""
    hour = created.hour % 12 or 12
    return (f"{MONTHS[created.month - 1]} {created.day}, {created.year}, {hour}:{created.minute:02d} "
            f"{'AM' if created.hour < 12 else 'PM'}")


def clean(text: str) -> str:
    """One line of plain text: no control or text-reordering characters, single spaces."""
    kept = "".join(" " if unicodedata.category(c) == "Cc" else c for c in text if c not in HIDDEN)
    return " ".join(kept.split())


def _folded(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in decomposed if unicodedata.category(c) not in ("Mn", "Cf"))  # accents, zero-widths
    return " ".join(plain.casefold().split())


def is_private_title(title: str) -> bool:
    folded = _folded(title)
    return any(hint in folded for hint in PRIVATE_HINTS)


def source(title: str, app: str) -> str:
    """The window title, or the app if there's no title (or it looks like private browsing)."""
    title, app = clean(title), clean(app)
    return title if title and not is_private_title(title) else app


def stamp_parts(title: str, app: str, created: dt.datetime) -> tuple[str, str]:
    """(where, when): where may be empty."""
    return source(title, app), when(created)


def stamp_text(title: str, app: str, created: dt.datetime) -> str:
    where, time = stamp_parts(title, app, created)
    return f"{where}{SEPARATOR}{time}" if where else time


def fitted(where: str, time: str, metrics: QFontMetricsF, room: float) -> str:
    """The stamp in at most `room` pixels: the title is shortened with "…", never the date."""
    if not where:
        return time
    tail = SEPARATOR + time
    left = room - metrics.horizontalAdvance(tail)
    if left < metrics.horizontalAdvance("…") * 4:
        return time
    short = metrics.elidedText(where, Qt.ElideRight, left)
    return short + tail if short and short != "…" else time
