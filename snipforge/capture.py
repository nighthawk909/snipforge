"""Freeze-frame capture overlay.

One borderless overlay per monitor shows a frozen screenshot. Hovering
highlights the window under the cursor (click to take it), dragging selects a
region, Enter takes the whole monitor. A magnifier shows exact pixels.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from . import theme

DIM = QColor(0, 0, 0, 120)
DRAG_THRESHOLD = 4
MAG_SAMPLE = 15  # physical pixels sampled around the cursor
MAG_SIZE = 120


def _span(a: QPoint, b: QPoint) -> QRect:
    """Rectangle between two drag points. QRect(a, b) includes both end pixels, which made a
    100-pixel drag capture 101 pixels."""
    return QRect(min(a.x(), b.x()), min(a.y(), b.y()), abs(b.x() - a.x()), abs(b.y() - a.y()))


class _ScreenOverlay(QWidget):
    selected = Signal(QImage)
    cancelled = Signal()

    def __init__(self, screen, pixmap: QPixmap, window_rects: list[QRect]) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self._pixmap = pixmap
        self._image = pixmap.toImage()
        self._dpr = pixmap.devicePixelRatio() or 1.0
        self.setGeometry(screen.geometry())
        self._windows = self._to_local(screen.geometry().topLeft(), window_rects)
        self._press: QPoint | None = None
        self._current: QPoint | None = None
        self._dragging = False
        self._cursor: QPoint | None = None
        self._hover: QRect | None = None
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_DeleteOnClose)

    def _to_local(self, origin: QPoint, rects: list[QRect]) -> list[QRect]:
        local = []
        bounds = QRect(0, 0, self.width(), self.height())
        for r in rects:
            mapped = QRect(round((r.x() - origin.x()) / self._dpr), round((r.y() - origin.y()) / self._dpr),
                           round(r.width() / self._dpr), round(r.height() / self._dpr)).intersected(bounds)
            if not mapped.isEmpty():
                local.append(mapped)
        local.append(bounds)  # hovering the desktop offers the whole monitor
        return local

    # ------------------------------------------------------------------ geometry
    def _selection(self) -> QRect | None:
        if self._dragging and self._press is not None and self._current is not None:
            return _span(self._press, self._current).intersected(self.rect())
        return self._hover

    def _window_at(self, pos: QPoint) -> QRect | None:
        return next((r for r in self._windows if r.contains(pos)), None)

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
            self._paint_hint(p)
        if self._cursor is not None:
            self._paint_magnifier(p, self._cursor)

    def _outside(self, sel: QRect) -> list[QRect]:
        r = self.rect()
        return [
            QRect(r.left(), r.top(), r.width(), sel.top() - r.top()),
            QRect(r.left(), sel.bottom() + 1, r.width(), r.bottom() - sel.bottom()),
            QRect(r.left(), sel.top(), sel.left() - r.left(), sel.height()),
            QRect(sel.right() + 1, sel.top(), r.right() - sel.right(), sel.height()),
        ]

    def _paint_hint(self, p: QPainter) -> None:
        text = "Click a window  •  drag a region  •  Enter for full screen  •  Esc to cancel"
        p.setFont(QFont("Segoe UI", 10))
        w = p.fontMetrics().horizontalAdvance(text) + 28
        box = QRectF((self.width() - w) / 2, 18, w, 32)
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
            if (pos - self._press).manhattanLength() >= DRAG_THRESHOLD:
                self._dragging = True
        else:
            self._hover = self._window_at(pos)
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
            if self._dragging else self._window_at(self._press)
        self._press = self._current = None
        self._dragging = False
        if sel is None or sel.width() < DRAG_THRESHOLD or sel.height() < DRAG_THRESHOLD:
            self.update()
            return
        self._emit(sel)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self.cancelled.emit()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._emit(self.rect())

    def _emit(self, sel: QRect) -> None:
        image = self._image.copy(self._to_physical(sel))
        image.setDevicePixelRatio(1.0)
        self.selected.emit(image)


class CaptureSession:
    """Owns the overlays for one capture; calls back with the image or None."""

    def __init__(self, on_done) -> None:
        self._on_done = on_done
        self._overlays: list[_ScreenOverlay] = []
        self._finished = False

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
            from .winrects import visible_windows
            windows = visible_windows()
        except OSError:
            windows = []
        for screen in QGuiApplication.screens():
            overlay = _ScreenOverlay(screen, screen.grabWindow(0), windows)
            overlay.selected.connect(self._finish)
            overlay.cancelled.connect(lambda: self._finish(None))
            self._overlays.append(overlay)
        active = QGuiApplication.screenAt(QCursor.pos())
        for overlay in self._overlays:
            overlay.showFullScreen()
            overlay.raise_()
            if overlay.screen() is active:
                overlay.activateWindow()
                overlay.setFocus()

    def _finish(self, image: QImage | None) -> None:
        if self._finished:
            return
        self._finished = True
        for overlay in self._overlays:
            overlay.close()
        self._overlays.clear()
        self._on_done(image)
