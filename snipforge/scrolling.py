"""Scrolling capture: scroll an area to the end, grabbing and stitching as it goes.

The pointer is put over the area and the wheel turned (snipforge.scroll_input), the area grabbed
after each scroll, and the frames stitched (snipforge.stitch). The step grows to about 60% of the
area once the first scroll shows how far one wheel notch goes, so every frame overlaps the last.
It stops at the end of the page, at the height limit, after MAX_STEPS, when the person moves the
mouse (the pointer isn't where it was put), or when stop() is called (the snip key pressed again).
A small pill says what's happening; it never takes focus or the mouse and stays out of the frames.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from . import scroll_input
from .countdown import exclude_from_capture
from .icons import icon
from .stitch import Stitcher

log = logging.getLogger(__name__)
SETTLE_MS = 300  # after each scroll (and before the first grab): smooth scrolling and repaint finish
FIRST_NOTCHES = 2
MAX_NOTCHES = 10
STEP_SHARE = 0.6  # of the area's height per scroll, so frames always overlap
MAX_STEPS = 300
RETRIES = {"mismatch": 2, "still": 1}  # extra looks before believing a frame (mid-redraw, slow app)
POINTER_SLACK = 3  # pixels the pointer may drift before it counts as the person moving it


class ScrollStatus(QWidget):
    """'Scrolling… move the mouse to stop': informational only, never in the way."""

    def __init__(self, screen, avoid: QRect) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        card = QFrame(self)
        card.setObjectName("Countdown")  # the same pill as the delay countdown
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 10, 16, 10)
        row.setSpacing(10)
        badge = QLabel(card)
        badge.setPixmap(icon("scroll").pixmap(QSize(20, 20)))
        row.addWidget(badge)
        self._label = QLabel("Scrolling… move the mouse to stop", card)
        self._label.setObjectName("CountdownText")
        row.addWidget(self._label)
        self.setAccessibleName(self._label.text())
        self.adjustSize()
        area = screen.availableGeometry()
        x = area.x() + (area.width() - self.width()) // 2
        top = area.y() + 18
        # Out of the way of the area being captured where possible (it also asks Windows to keep
        # it out of screenshots, which older Windows can't do).
        if QRect(x, top, self.width(), self.height()).intersects(avoid):
            top = area.bottom() - self.height() - 18
        self.move(x, top)

    def text(self) -> str:
        return self._label.text()

    def show_now(self) -> None:
        self.show()
        exclude_from_capture(self)


class ScrollCapture(QObject):
    finished = Signal(object)  # the stitched QImage, or None when cancelled

    def __init__(self, screen, rect: QRect, *, grab=None, scroller=scroll_input, settle_ms: int = SETTLE_MS,
                 max_steps: int = MAX_STEPS, parent: QObject | None = None) -> None:
        """`rect`: the area in `screen`'s own physical pixels (as in a grab of that screen)."""
        super().__init__(parent)
        self._screen = screen
        self._rect = QRect(rect)
        self._grab = grab or self._grab_screen
        self._scroller = scroller
        self._settle_ms = settle_ms
        self._max_steps = max_steps
        self._stitcher = Stitcher()
        self._target = screen.geometry().topLeft() + rect.center()  # physical, whole desktop
        self._notches = FIRST_NOTCHES
        self._steps = 0
        self._retries = 0
        self._saved_pointer = None
        self._done = False
        self.status: ScrollStatus | None = None
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self._step)

    def _grab_screen(self) -> QImage:
        return self._screen.grabWindow(0).toImage().copy(self._rect)

    def _logical_area(self) -> QRect:
        dpr = self._screen.devicePixelRatio() or 1.0
        origin = self._screen.geometry().topLeft()
        return QRect(origin.x() + round(self._rect.x() / dpr), origin.y() + round(self._rect.y() / dpr),
                     round(self._rect.width() / dpr), round(self._rect.height() / dpr))

    def start(self) -> None:
        self._saved_pointer = self._scroller.pointer_position()
        self.status = ScrollStatus(self._screen, self._logical_area())
        self.status.show_now()
        self._timer.start(self._settle_ms)

    def stop(self) -> None:
        """Finish now with what's been stitched so far."""
        self._finish(keep=True)

    def cancel(self) -> None:
        self._finish(keep=False)

    def _step(self) -> None:
        if self._done:
            return
        try:
            frame = self._grab()
            frame.setDevicePixelRatio(1.0)
            added = self._stitcher.add(frame)
        except Exception:  # noqa: BLE001 - keep what we have rather than lose the snip
            log.exception("Scrolling capture failed while grabbing")
            self._finish(keep=True)
            return
        if self._steps:
            if not added and self._retries < RETRIES.get(self._stitcher.outcome, 0):
                self._retries += 1  # look again without scrolling: it may still be redrawing
                self._timer.start(self._settle_ms)
                return
            self._retries = 0
            if not added or self._steps >= self._max_steps:
                self._finish(keep=True)
                return
            if (self._scroller.pointer_position() - self._target).manhattanLength() > POINTER_SLACK:
                self._finish(keep=True, restore_pointer=False)  # the person took the mouse back
                return
            self._adapt(frame.height())
        try:
            self._scroller.place_pointer(self._target)
            self._scroller.scroll_down(self._notches)
        except OSError:
            log.exception("Couldn't scroll")
            self._finish(keep=True)
            return
        self._steps += 1
        self._timer.start(self._settle_ms)

    def _adapt(self, height: int) -> None:
        """Size the next scroll from how far the last one went."""
        moved = self._stitcher.last_shift
        if moved > 0:
            per_notch = moved / self._notches
            self._notches = max(1, min(MAX_NOTCHES, int(STEP_SHARE * height / per_notch)))

    def _finish(self, keep: bool, restore_pointer: bool = True) -> None:
        if self._done:
            return
        self._done = True
        self._timer.stop()
        if self.status is not None:
            self.status.close()
            self.status.deleteLater()
            self.status = None
        if restore_pointer and self._saved_pointer is not None and self._steps:
            try:
                self._scroller.place_pointer(self._saved_pointer)
            except OSError:
                log.info("Couldn't put the pointer back")
        image = None
        if keep:
            try:
                image = self._stitcher.result()
            except ValueError:
                image = None  # stopped before the first grab
        self.finished.emit(image)
