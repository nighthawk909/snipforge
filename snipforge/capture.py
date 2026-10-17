"""Freeze-frame capture overlay.

One borderless overlay per monitor shows a frozen screenshot, with the capture toolbar on the
monitor the pointer is on. The toolbar's mode decides what a click or drag takes: Area (drag an
area, or click a window), Window (click a window) or Full screen (click a monitor). Enter always
takes the whole monitor. A magnifier shows exact pixels.
"""
from __future__ import annotations

import logging
from dataclasses import replace

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from . import prefs, theme
from .capture_toolbar import MODES, CaptureToolbar

DIM = QColor(0, 0, 0, 120)
DRAG_THRESHOLD = 4
TOOLBAR_TOP = 18
HINT_BOTTOM = 40  # gap between the hint and the bottom of the monitor
SOURCE_OVERLAP = 0.9  # share of a snip that must lie inside one window to credit that window
PRIVATE_MARKERS = ("inprivate", "incognito", "private browsing")


def _private(title: str) -> bool:
    lowered = title.lower()
    return any(marker in lowered for marker in PRIVATE_MARKERS)
MAG_SAMPLE = 15  # physical pixels sampled around the cursor
MAG_SIZE = 120


def _span(a: QPoint, b: QPoint) -> QRect:
    """Rectangle between two drag points. QRect(a, b) includes both end pixels, which made a
    100-pixel drag capture 101 pixels."""
    return QRect(min(a.x(), b.x()), min(a.y(), b.y()), abs(b.x() - a.x()), abs(b.y() - a.y()))


class _ScreenOverlay(QWidget):
    selected = Signal(QImage)
    cancelled = Signal()
    mode_changed = Signal(str)  # the user picked another mode here (toolbar or letter key)

    def __init__(self, screen, pixmap: QPixmap, window_rects: list[QRect], sources: list | None = None,
                 *, mode: str = "area", toolbar: bool = False) -> None:
        """`sources` (optional) describes each of `window_rects`: a winrects.WindowInfo, same order.
        `toolbar`: show the capture toolbar on this monitor (the one the pointer is on)."""
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self._pixmap = pixmap
        self._image = pixmap.toImage()
        self._dpr = pixmap.devicePixelRatio() or 1.0
        self.setGeometry(screen.geometry())
        origin = screen.geometry().topLeft()
        self._windows = self._to_local(origin, window_rects)
        self._sources = [(local, info) for r, info in zip(window_rects, sources or [], strict=False)
                         if (local := self._map(origin, r)) is not None]
        self.last_source = None  # the window the latest selection came from, if any
        self._magnifier = prefs.magnifier()
        self._press: QPoint | None = None
        self._current: QPoint | None = None
        self._dragging = False
        self._cursor: QPoint | None = None
        self._hover: QRect | None = None
        self._mode = mode if mode in MODES else "area"
        self.toolbar: CaptureToolbar | None = None
        if toolbar:
            self.toolbar = CaptureToolbar(self._mode, self)
            self.toolbar.mode_picked.connect(self._pick_mode)
            self.toolbar.cancel_clicked.connect(self.cancelled)
            self.toolbar.installEventFilter(self)
            self._place_toolbar()
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_DeleteOnClose)

    # ------------------------------------------------------------------ mode and toolbar
    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Switch modes without announcing it (the session uses this to keep monitors in step)."""
        if mode not in MODES:
            raise ValueError(f"unknown capture mode {mode!r}")
        self._mode = mode
        self._hover = self._target_at(self._cursor) if self._cursor is not None else None
        if self.toolbar is not None:
            self.toolbar.set_mode(mode)
        self.update()

    def _pick_mode(self, mode: str) -> None:
        if mode != self._mode:
            self.set_mode(mode)
            self.mode_changed.emit(mode)

    def hint(self) -> str:
        return MODES[self._mode].hint

    def _place_toolbar(self) -> None:
        if self.toolbar is None:
            return
        self.toolbar.adjustSize()
        self.toolbar.move((self.width() - self.toolbar.width()) // 2, TOOLBAR_TOP)

    def resizeEvent(self, e) -> None:  # noqa: N802 - Qt API
        self._place_toolbar()
        super().resizeEvent(e)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if watched is self.toolbar and event.type() == QEvent.Enter:
            self._cursor = None  # no magnifier or window highlight behind the toolbar
            if self._press is None:
                self._hover = None
            self.update()
        return False

    def _map(self, origin: QPoint, r: QRect) -> QRect | None:
        """A window frame in physical screen pixels -> this overlay's coordinates (None if off-screen)."""
        bounds = QRect(0, 0, self.width(), self.height())
        mapped = QRect(round((r.x() - origin.x()) / self._dpr), round((r.y() - origin.y()) / self._dpr),
                       round(r.width() / self._dpr), round(r.height() / self._dpr)).intersected(bounds)
        return None if mapped.isEmpty() else mapped

    def _to_local(self, origin: QPoint, rects: list[QRect]) -> list[QRect]:
        local = [m for r in rects if (m := self._map(origin, r)) is not None]
        local.append(QRect(0, 0, self.width(), self.height()))  # hovering the desktop offers the whole monitor
        return local

    def _source_for(self, sel: QRect):
        """The window a snip came from: the topmost window that holds nearly all of the selection
        (so a whole-monitor snip isn't credited to whatever sits in the middle). Private browsing
        windows keep their app name but never their title."""
        area = sel.width() * sel.height()
        if area <= 0:
            return None
        for local, info in self._sources:
            overlap = local.intersected(sel)
            if overlap.width() * overlap.height() >= SOURCE_OVERLAP * area:
                return info if not _private(info.title) else replace(info, title="")
            if local.contains(sel.center()):
                return None  # the window on top doesn't hold the snip: don't guess
        return None

    # ------------------------------------------------------------------ geometry
    def _selection(self) -> QRect | None:
        if self._dragging and self._press is not None and self._current is not None:
            return _span(self._press, self._current).intersected(self.rect())
        return self._hover

    def _target_at(self, pos: QPoint) -> QRect | None:
        """What a click at `pos` takes in the current mode."""
        if self._mode == "screen":
            return self.rect()
        # The last entry is the whole monitor, offered when Area mode hovers the desktop.
        windows = self._windows if self._mode == "area" else self._windows[:-1]
        return next((r for r in windows if r.contains(pos)), None)

    def _to_physical(self, r: QRect) -> QRect:
        return QRect(round(r.x() * self._dpr), round(r.y() * self._dpr),
                     round(r.width() * self._dpr), round(r.height() * self._dpr))

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.drawPixmap(self.rect(), self._pixmap)
        sel = self._selection()
        p.setPen(Qt.NoPen)
        p.setBrush(DIM)
        if sel is None or sel.isEmpty():
            p.drawRect(self.rect())
        else:
            for r in self._outside(sel):
                p.drawRect(r)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(theme.current().accent.fill), 2 if not self._dragging else 1))
            p.drawRect(sel.adjusted(0, 0, -1, -1))
            self._paint_size_label(p, sel)
        if not self._dragging and self._press is None:
            self._paint_hint(p, self.hint())
        if self._cursor is not None:
            if self._magnifier:
                self._paint_magnifier(p, self._cursor)

    def _outside(self, sel: QRect) -> list[QRect]:
        r = self.rect()
        return [
            QRect(r.left(), r.top(), r.width(), sel.top() - r.top()),
            QRect(r.left(), sel.bottom() + 1, r.width(), r.bottom() - sel.bottom()),
            QRect(r.left(), sel.top(), sel.left() - r.left(), sel.height()),
            QRect(sel.right() + 1, sel.top(), r.right() - sel.right(), sel.height()),
        ]

    def _paint_hint(self, p: QPainter, text: str) -> None:
        p.setFont(QFont("Segoe UI", 10))
        w = p.fontMetrics().horizontalAdvance(text) + 28
        box = QRectF((self.width() - w) / 2, self.height() - HINT_BOTTOM - 32, w, 32)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 20, 20, 225))
        p.drawRoundedRect(box, 8, 8)
        p.setPen(QColor("#f0f0f0"))
        p.drawText(box, Qt.AlignCenter, text)

    def _paint_size_label(self, p: QPainter, sel: QRect) -> None:
        text = f"{round(sel.width() * self._dpr)} × {round(sel.height() * self._dpr)}"
        p.setFont(QFont("Segoe UI", 9))
        w = p.fontMetrics().horizontalAdvance(text) + 14
        h = p.fontMetrics().height() + 8
        y = sel.top() - h - 6 if sel.top() > h + 10 else sel.top() + 6
        box = QRectF(sel.left(), y, w, h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 20, 20, 230))
        p.drawRoundedRect(box, 5, 5)
        p.setPen(Qt.white)
        p.drawText(box, Qt.AlignCenter, text)

    def _paint_magnifier(self, p: QPainter, pos: QPoint) -> None:
        px, py = round(pos.x() * self._dpr), round(pos.y() * self._dpr)
        half = MAG_SAMPLE // 2
        sample = self._image.copy(px - half, py - half, MAG_SAMPLE, MAG_SAMPLE)
        x = pos.x() + 24 if pos.x() + 24 + MAG_SIZE < self.width() else pos.x() - 24 - MAG_SIZE
        y = pos.y() + 24 if pos.y() + 24 + MAG_SIZE + 22 < self.height() else pos.y() - 24 - MAG_SIZE - 22
        target = QRect(x, y, MAG_SIZE, MAG_SIZE)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        p.drawImage(target, sample)
        cell = MAG_SIZE / MAG_SAMPLE
        p.setPen(QPen(QColor(theme.current().accent.fill), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(x + half * cell, y + half * cell, cell, cell))
        p.setPen(QPen(QColor("#ffffff"), 1))
        p.drawRect(target.adjusted(0, 0, -1, -1))
        p.setPen(QPen(QColor(0, 0, 0, 160), 1))
        p.drawRect(target.adjusted(-1, -1, 0, 0))
        color = self._image.pixelColor(px, py) if self._image.rect().contains(px, py) else QColor()
        label = QRectF(x, y + MAG_SIZE, MAG_SIZE, 22)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 20, 20, 235))
        p.drawRect(label)
        p.setPen(QColor("#f0f0f0"))
        p.setFont(QFont("Consolas", 8))
        p.drawText(label, Qt.AlignCenter, f"{px},{py}  {color.name().upper() if color.isValid() else ''}")

    # ------------------------------------------------------------------ input
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.RightButton:
            self.cancelled.emit()
            return
        if e.button() == Qt.LeftButton:
            self._press = self._current = e.position().toPoint()
            self._dragging = False
            self.update()

    def mouseMoveEvent(self, e) -> None:
        pos = e.position().toPoint()
        self._cursor = pos
        if self._press is not None:
            self._current = pos
            if self._mode == "area" and (pos - self._press).manhattanLength() >= DRAG_THRESHOLD:
                self._dragging = True
                if self.toolbar is not None:
                    self.toolbar.hide()  # out of the way of the area being chosen
        else:
            self._hover = self._target_at(pos)
        self.update()

    def leaveEvent(self, _e) -> None:
        self._cursor = None
        if self._press is None:
            self._hover = None
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.LeftButton or self._press is None:
            return
        sel = _span(self._press, e.position().toPoint()).intersected(self.rect()) \
            if self._dragging else self._target_at(self._press)
        self._press = self._current = None
        self._dragging = False
        if self.toolbar is not None:
            self.toolbar.show()
        if sel is None or sel.width() < DRAG_THRESHOLD or sel.height() < DRAG_THRESHOLD:
            self.update()
            return
        self._emit(sel)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self.cancelled.emit()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._emit(self.rect())
        elif e.modifiers() in (Qt.NoModifier, Qt.ShiftModifier) and (
                mode := next((k for k, spec in MODES.items() if e.key() == ord(spec.key)), None)):
            self._pick_mode(mode)
        else:
            super().keyPressEvent(e)

    def _emit(self, sel: QRect) -> None:
        image = self._image.copy(self._to_physical(sel))
        image.setDevicePixelRatio(1.0)
        self.last_source = self._source_for(sel)
        self.selected.emit(image)


class CaptureSession:
    """Owns the overlays for one capture; calls back with the image or None."""

    def __init__(self, on_done) -> None:
        self._on_done = on_done
        self._overlays: list[_ScreenOverlay] = []
        self._mode = "area"
        self._finished = False
        self.source = None  # the window the snip came from (winrects.WindowInfo), when known

    def start(self) -> None:
        try:
            self._show_overlays()
        except Exception:  # never leave a half-open session that blocks the next snip
            logging.getLogger(__name__).exception("Capture failed to start")
            self._finish(None)
            return
        if not self._overlays:
            self._finish(None)

    def _show_overlays(self) -> None:
        try:
            from .winrects import window_infos
            infos = window_infos()
        except OSError:
            infos = []
        windows = [info.rect for info in infos]
        self._mode = prefs.capture_mode()
        # The toolbar and the keyboard go to the monitor the pointer is on (the main one if the
        # pointer is somewhere Windows doesn't report, e.g. while a remote session reconnects).
        active = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        focus = None
        for screen in QGuiApplication.screens():
            overlay = _ScreenOverlay(screen, screen.grabWindow(0), windows, infos,
                                     mode=self._mode, toolbar=screen is active)
            overlay.selected.connect(lambda image, o=overlay: self._finish(image, o.last_source))
            overlay.cancelled.connect(lambda: self._finish(None))
            overlay.mode_changed.connect(self._set_mode)
            self._overlays.append(overlay)
            if screen is active:
                focus = overlay
        for overlay in self._overlays:
            overlay.showFullScreen()
            overlay.raise_()
        if focus is not None:
            focus.activateWindow()
            focus.setFocus()

    def _set_mode(self, mode: str) -> None:
        """A mode picked on one monitor applies to all of them, and to the next snip."""
        self._mode = mode
        for overlay in self._overlays:
            overlay.set_mode(mode)
        prefs.set_capture_mode(mode)

    def _finish(self, image: QImage | None, source=None) -> None:
        if self._finished:
            return
        self._finished = True
        self.source = source if image is not None else None
        for overlay in self._overlays:
            overlay.close()
        self._overlays.clear()
        self._on_done(image)
