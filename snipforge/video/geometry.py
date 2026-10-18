"""Physical desktop pixels (what DXGI records) and Qt's coordinates (what windows are placed by).

Qt keeps each monitor's top-left where Windows has it, in physical pixels, and measures within a
monitor in its own scaled pixels: logical = top-left + (physical - top-left) / scale.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QGuiApplication


def physical_geometry(screen) -> QRect:
    """A monitor's whole area in physical desktop pixels."""
    g, ratio = screen.geometry(), screen.devicePixelRatio()
    return QRect(g.topLeft(), QPoint(g.x() + round(g.width() * ratio) - 1, g.y() + round(g.height() * ratio) - 1))


def screen_of(physical: QRect):
    """The monitor holding the middle of `physical`, or the main one."""
    middle = physical.center()
    return next((s for s in QGuiApplication.screens() if physical_geometry(s).contains(middle)),
                QGuiApplication.primaryScreen())


def to_logical(physical: QRect, screen=None) -> QRect:
    """`physical` (desktop pixels) in Qt's coordinates, on `screen` (found if not given)."""
    screen = screen or screen_of(physical)
    origin, ratio = screen.geometry().topLeft(), screen.devicePixelRatio()
    left = origin.x() + round((physical.x() - origin.x()) / ratio)
    top = origin.y() + round((physical.y() - origin.y()) / ratio)
    return QRect(left, top, round(physical.width() / ratio), round(physical.height() / ratio))


def place_beside(area: QRect, size, bounds: QRect, gap: int = 10) -> QPoint:
    """Where a window of `size` goes next to `area` (all logical): centred below it, or above it if
    there's no room below, or else inside it at the bottom; always within `bounds`. (Plain edges,
    not QRect's bottom() and right(), which are one pixel short.)"""
    below, bounds_bottom = area.y() + area.height(), bounds.y() + bounds.height()
    x = area.x() + (area.width() - size.width()) // 2
    if below + gap + size.height() <= bounds_bottom:
        y = below + gap
    elif area.y() - gap - size.height() >= bounds.y():
        y = area.y() - gap - size.height()
    else:
        y = below - gap - size.height()
    x = max(bounds.x(), min(x, bounds.x() + bounds.width() - size.width()))
    y = max(bounds.y(), min(y, bounds_bottom - size.height()))
    return QPoint(x, y)
