"""Scrolling capture: scroll an area to the end, grabbing and stitching as it goes.

Each step: put the pointer over the part that scrolls and turn the wheel (snipforge.scroll_input),
move the pointer off the area (so no hover highlight ends up in the snip), wait until the area stops
changing, grab it and stitch it on (snipforge.stitch). The step grows to about 60% of the part that
scrolls once the first scroll shows how far one wheel notch goes, so every view overlaps the last,
and the stitcher is told how far each scroll should have gone (it settles ties on repetitive pages).

It stops at the end of the page; at the size or step limit; when the person moves the mouse; when
a key is held (a wheel turn would zoom or scroll sideways); when another window comes in front; or
on stop() (the snip key pressed again: keep what's stitched) or cancel(). It never scrolls the
desktop or the taskbar. A view that doesn't line up is looked at again before it ends the capture.
When it ends early, `note` says why in a sentence for the person. A small pill shows what's
happening; it never takes focus or the mouse and stays out of the frames.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from . import scroll_input
from .countdown import exclude_from_capture
from .icons import icon
from .stitch import Stitcher

log = logging.getLogger(__name__)
SETTLE_MS = 150  # at least this long after a scroll before the first look
POLL_MS = 80  # then look again until two looks match (the page has stopped moving)...
MAX_SETTLE_MS = 1500  # ...or this long has passed (a video or spinner never stops)
FIRST_NOTCHES = 1  # small, so the first scroll can't overshoot the view
MAX_NOTCHES = 10
STEP_SHARE = 0.6  # of the part that scrolls, per scroll, so views always overlap
MAX_STEPS = 300
POINTER_SLACK = 3  # pixels the pointer may drift before it counts as the person moving it
PARK_GAP = 12  # pixels outside the area where the pointer waits during a grab
RETRIES = {"mismatch": 2}  # extra looks at a view that doesn't line up (grabbed mid-redraw)
NOTES = {
    "mismatch": "Scrolling capture stopped early: the page changed while it was scrolling. "
                "The snip has everything up to that point.",
    "full": "Scrolling capture stopped at its size limit. The snip has everything up to that point.",
    "keys": "Scrolling capture stopped because a key was held down.",
    "covered": "Scrolling capture stopped because another window came in front.",
    "nothing": "That area didn't scroll, so the snip is just what was showing.",
    "desktop": "The desktop and taskbar don't scroll, so the snip is just what was showing.",
    "error": "Scrolling capture ran into a problem. The snip has everything it got before that.",
}


class ScrollStatus(QWidget):
    """'Scrolling… move the mouse or press the snip key to stop': informational only."""

    TEXT = "Scrolling… move the mouse or press the snip key to stop"

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
        self._label = QLabel(self.TEXT, card)
        self._label.setObjectName("CountdownText")
        self._label.setAccessibleName(self.TEXT)
        row.addWidget(self._label)
        self.setAccessibleName(self.TEXT)
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
                 poll_ms: int = POLL_MS, max_settle_ms: int = MAX_SETTLE_MS, max_steps: int = MAX_STEPS,
                 parent: QObject | None = None) -> None:
        """`rect`: the area in `screen`'s own physical pixels (as in a grab of that screen)."""
        super().__init__(parent)
        self._screen = screen
        self._rect = QRect(rect)
        self._grab = grab or self._grab_screen
        self._scroller = scroller
        self._settle_ms, self._poll_ms, self._max_settle_ms = settle_ms, poll_ms, max_settle_ms
        self._max_steps = max_steps
        self._stitcher = Stitcher()
        self._origin = screen.geometry().topLeft()  # physical: Qt keeps each screen's corner native
        self._target = self._origin + rect.center()  # where the wheel is turned (physical)
        self._park = self._parking_spot()
        self._notches = FIRST_NOTCHES
        self._per_notch = 0.0  # the furthest one notch has moved the page (the last scroll can be cut short)
        self._steps = 0
        self._retries = 0
        self._rechecked = False  # a scroll that seemed to do nothing has been tried once more
        self._window = 0  # the window being scrolled
        self._saved_pointer: QPoint | None = None
        self._placed: QPoint | None = None  # where the pointer was last put
        self._looks: list[QImage] = []
        self._waited = 0
        self._done = False
        self.reason = ""
        self.note: str | None = None  # why it ended early, for the person; None when it just finished
        self.status: ScrollStatus | None = None
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self._look)

    # ------------------------------------------------------------------ geometry
    def _grab_screen(self) -> QImage:
        return self._screen.grabWindow(0).toImage().copy(self._rect)

    def _screen_rect(self) -> QRect:
        dpr = self._screen.devicePixelRatio() or 1.0
        size = self._screen.geometry().size()
        return QRect(self._origin, QSize(round(size.width() * dpr), round(size.height() * dpr)))

    def _parking_spot(self) -> QPoint:
        """Just outside the area, where the pointer waits while the area is grabbed (so nothing in
        the area shows a hover effect). Its top-left corner if the area fills the screen."""
        area = self._rect.translated(self._origin)
        screen = self._screen_rect()
        gap = PARK_GAP
        spots = (QPoint(area.left() - gap, area.top() - gap), QPoint(area.right() + gap, area.top() - gap),
                 QPoint(area.left() - gap, area.bottom() + gap), QPoint(area.right() + gap, area.bottom() + gap))
        for spot in spots:
            if screen.contains(spot):
                return spot
        return area.topLeft()

    def _logical_area(self) -> QRect:
        dpr = self._screen.devicePixelRatio() or 1.0
        return QRect(self._origin.x() + round(self._rect.x() / dpr), self._origin.y() + round(self._rect.y() / dpr),
                     round(self._rect.width() / dpr), round(self._rect.height() / dpr))

    def _place(self, at: QPoint) -> None:
        self._scroller.place_pointer(at)
        self._placed = QPoint(at)

    def _person_moved_pointer(self) -> bool:
        if self._placed is None:
            return False
        return (self._scroller.pointer_position() - self._placed).manhattanLength() > POINTER_SLACK

    # ------------------------------------------------------------------ the loop
    def start(self) -> None:
        try:
            self._saved_pointer = self._scroller.pointer_position()
            self._window = self._scroller.window_at(self._target)
            self.status = ScrollStatus(self._screen, self._logical_area())
            self.status.show_now()
            self._place(self._park)
        except Exception:  # noqa: BLE001 - never leave the session waiting
            log.exception("Scrolling capture failed to start")
            self._finish(keep=False, reason="error")
            return
        self._settle()

    def stop(self) -> None:
        """Finish now with what's been stitched so far (at least what's showing)."""
        if not self._done and self._stitcher.outcome == "":
            try:
                self._stitcher.add(self._grab())
            except Exception:  # noqa: BLE001 - finish with nothing rather than not at all
                log.exception("Couldn't grab the area")
        self._finish(keep=True, reason="stopped")

    def cancel(self) -> None:
        self._finish(keep=False, reason="cancelled")

    def _settle(self) -> None:
        """Look at the area until it stops changing (or long enough has passed)."""
        self._looks, self._waited = [], 0
        self._timer.start(self._settle_ms)
        self._waited += self._settle_ms

    def _look(self) -> None:
        if self._done:
            return
        try:
            frame = self._grab()
            frame.setDevicePixelRatio(1.0)
        except Exception:  # noqa: BLE001 - keep what we have rather than lose the snip
            log.exception("Scrolling capture failed while grabbing")
            self._finish(keep=True, reason="error")
            return
        settled = bool(self._looks) and frame == self._looks[-1]
        self._looks = [frame]
        if settled or self._waited >= self._max_settle_ms:
            self._step(frame)
        else:
            self._timer.start(self._poll_ms)
            self._waited += max(self._poll_ms, 1)

    def _step(self, frame: QImage) -> None:
        try:
            added = self._stitcher.add(frame)
        except Exception:  # noqa: BLE001
            log.exception("Scrolling capture failed while stitching")
            self._finish(keep=True, reason="error")
            return
        if self._steps:
            outcome = self._stitcher.outcome
            if not added and self._retries < RETRIES.get(outcome, 0):
                self._retries += 1  # look again without scrolling: it may still be redrawing
                self._settle()
                return
            self._retries = 0
            if not added and outcome == "still" and not self._rechecked:
                # On a repetitive page (identical lines) a scroll of a whole number of lines looks
                # like no scroll at all: go back and try one notch more before calling it the end.
                self._rechecked = True
                self._scroll(back=True)
                return
            if added:
                self._rechecked = False
            if not added:
                never_moved = self._stitcher.last_shift == 0
                self._finish(keep=True, reason="nothing" if outcome == "still" and never_moved
                             else ("end" if outcome == "still" else outcome))
                return
        if self._person_moved_pointer():
            self._finish(keep=True, reason="moved", restore_pointer=False)
            return
        if self._steps >= self._max_steps:
            self._finish(keep=True, reason="full")
            return
        if self._steps == 0 and self._scroller.is_desktop(self._window):
            self._finish(keep=True, reason="desktop")
            return
        if self._scroller.modifiers_down():
            self._finish(keep=True, reason="keys")
            return
        if self._scroller.window_at(self._target) != self._window:
            self._finish(keep=True, reason="covered")
            return
        if self._steps:
            self._adapt(frame.height())
        self._scroll()

    def _scroll(self, back: bool = False) -> None:
        """Turn the wheel with the pointer over the part that scrolls, then move it off the area.
        `back`: first undo the last scroll, then go one notch further than it did."""
        try:
            self._place(self._target)
            if back:
                self._scroller.scroll_up(self._notches)
                self._notches += 1
                if self._per_notch:
                    self._stitcher.expected = round(self._notches * self._per_notch)
            self._scroller.scroll_down(self._notches)
            self._place(self._park)
        except OSError:
            log.exception("Couldn't scroll")
            self._finish(keep=True, reason="error")
            return
        self._steps += 1
        self._settle()

    def _adapt(self, height: int) -> None:
        """Size the next scroll from how far the last one went, against the part that scrolls (not
        a sticky header or footer), and aim the wheel at the middle of that part."""
        s = self._stitcher
        band = max(1, height - s.header_rows - s.footer_rows)
        if s.last_shift > 0:
            self._per_notch = max(self._per_notch, s.last_shift / self._notches)
            self._notches = max(1, min(MAX_NOTCHES, int(STEP_SHARE * band / self._per_notch)))
            s.expected = round(self._notches * self._per_notch)
        self._target = self._origin + QPoint(self._rect.center().x(), self._rect.top() + s.header_rows + band // 2)

    def _finish(self, keep: bool, reason: str, restore_pointer: bool = True) -> None:
        if self._done:
            return
        self._done = True
        self.reason = reason
        self.note = NOTES.get(reason)
        image = None
        try:
            self._timer.stop()
            if self.status is not None:
                self.status.close()
                self.status.deleteLater()
                self.status = None
            if restore_pointer and self._saved_pointer is not None and not self._person_moved_pointer():
                self._scroller.place_pointer(self._saved_pointer)
            if keep:
                image = self._stitcher.result()
                if image.isNull():
                    image = None
        except Exception:  # noqa: BLE001 - whatever happens, the session hears back
            log.exception("Scrolling capture failed while finishing")
            self.note = self.note or NOTES["error"]
        finally:
            self.finished.emit(image)
