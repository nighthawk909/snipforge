"""Where things are, for the capture overlay: the area between two drag points, the window a snip
came from, and a snip's area kept for Repeat last snip (with enough about its monitor to find that
monitor again)."""
from __future__ import annotations

import math
from dataclasses import replace

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QGuiApplication

from . import prefs

SOURCE_OVERLAP = 0.9  # share of a snip that must lie inside one window to credit that window
PRIVATE_MARKERS = ("inprivate", "incognito", "private browsing")


def span(a: QPoint, b: QPoint) -> QRect:
    """Rectangle between two drag points. QRect(a, b) includes both end pixels, which made a
    100-pixel drag capture 101 pixels."""
    return QRect(min(a.x(), b.x()), min(a.y(), b.y()), abs(b.x() - a.x()), abs(b.y() - a.y()))


def _private(title: str) -> bool:
    lowered = title.lower()
    return any(marker in lowered for marker in PRIVATE_MARKERS)


def source_for(sources: list, sel: QRect):
    """The window a snip came from: the topmost of `sources` ((rect, winrects.WindowInfo), topmost
    first, in the same coordinates as `sel`) that holds nearly all of the selection (so a
    whole-monitor snip isn't credited to whatever sits in the middle). Private browsing windows keep
    their app name but never their title."""
    area = sel.width() * sel.height()
    if area <= 0:
        return None
    for local, info in sources:
        overlap = local.intersected(sel)
        if overlap.width() * overlap.height() >= SOURCE_OVERLAP * area:
            return info if not _private(info.title) else replace(info, title="")
        if local.contains(sel.center()):
            return None  # the window on top doesn't hold the snip: don't guess
    return None


def area_on(screen, rect: QRect) -> prefs.LastArea:
    """A snip's area, with enough about its monitor to find that monitor again (Repeat last snip)."""
    return prefs.LastArea(screen.name(), _geometry(screen), round(screen.devicePixelRatio(), 3),
                          (rect.x(), rect.y(), rect.width(), rect.height()))


def screen_for(area: prefs.LastArea):
    """The monitor `area` was snipped on, if it's still there exactly as it was: same name, place,
    size and scale. None otherwise, so a repeat never lands on a look-alike monitor or on a monitor
    whose resolution or scaling changed."""
    matches = [s for s in QGuiApplication.screens()
               if (s.name(), _geometry(s), round(s.devicePixelRatio(), 3)) == (area.screen, area.geometry, area.scale)]
    return matches[0] if len(matches) == 1 else None


def _geometry(screen) -> tuple[int, int, int, int]:
    g = screen.geometry()
    return g.x(), g.y(), g.width(), g.height()


def on_screen(area: prefs.LastArea) -> bool:
    """Whether Repeat last snip can take `area` again."""
    screen = screen_for(area)
    if screen is None:
        return False
    w, h = area.geometry[2] * area.scale, area.geometry[3] * area.scale
    return QRect(0, 0, math.ceil(w), math.ceil(h)).contains(QRect(*area.rect))
