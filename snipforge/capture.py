"""Freeze-frame capture overlay.

One borderless overlay per monitor shows a frozen screenshot, with the capture toolbar on the
monitor the pointer is on. The toolbar's mode decides what a click or drag takes: Area (drag an
area, or click a window), Window (click a window), Full screen (click a monitor) or Fixed size (a
frame of an exact size follows the pointer; arrow keys nudge it). Enter takes the whole monitor, or
the frame in Fixed size. A magnifier shows exact pixels.

A delay counts down before the screen freezes (so menus and tooltips can be opened first), and
the mouse pointer, grabbed the moment the screen froze, can be drawn into the snip.

With Record switched on (the toolbar's first button, or R), the same choice picks an area to record
a video of: the session hands it, in physical desktop pixels, to `on_area` (snipforge/video).
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from . import pointer, prefs, scroll_input, theme
from .capture_area import area_on, on_screen, screen_for, source_for, span  # noqa: F401
from .capture_paint import paint_hint, paint_magnifier, paint_size_label
from .capture_toolbar import MODES, CaptureToolbar
from .countdown import Countdown
from .scrolling import ScrollCapture

DIM = QColor(0, 0, 0, 120)
DRAG_THRESHOLD = 4
DRAG_MODES = ("area", "scroll")  # modes where a drag chooses an area
FIXED_NUDGE, FIXED_NUDGE_SHIFT = 1, 10  # screen pixels an arrow key moves the Fixed size frame (Shift: more)
ARROWS = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}
TOOLBAR_TOP = 18
MIN_RECORD_SIDE = 48  # snipforge.video.recorder.MIN_SIDE (not imported: that loads the recorder)
NOTE_MS = 3000


def _grab_screen(screen) -> QPixmap:
    return screen.grabWindow(0)


def _pointer_unavailable_reason() -> str:
    if pointer.available():
        return "The pointer was hidden when the screen froze"
    return "Including the pointer isn't available on this computer yet"


class _ScreenOverlay(QWidget):
    selected = Signal(QImage)
    cancelled = Signal()
    mode_changed = Signal(str)  # the user picked another mode here (toolbar or letter key)
    delay_changed = Signal(int)  # the user picked a delay (seconds) on the toolbar
    scroll_requested = Signal(QRect)  # Scrolling mode: this area (overlay coordinates) is to be scrolled
    pointer_changed = Signal(bool)  # the user switched the pointer on or off here
    size_changed = Signal(int, int)  # the user picked Fixed size mode's size here (screen pixels)
    keyboard_taken = Signal()  # the pointer came to this monitor, and the keyboard came with it
    area_chosen = Signal(QRect)  # Record: this area, in physical desktop pixels, is to be recorded
    record_changed = Signal(bool)  # the user switched Record on or off here

    def __init__(self, screen, pixmap: QPixmap, window_rects: list[QRect], sources: list | None = None,
                 *, mode: str = "area", toolbar: bool = False, delay: int = 0,
                 pointer_shot: pointer.PointerShot | None = None, include_pointer: bool = False,
                 scrolling: bool | None = None, fixed_size: tuple[int, int] = prefs.DEFAULT_FIXED_SIZE,
                 record: bool = False, recordable: bool = False) -> None:
        """`sources` (optional) describes each of `window_rects`: a winrects.WindowInfo, same order.
        `toolbar`: show the capture toolbar on this monitor. `pointer_shot`: the pointer as it was
        when the screen froze, drawn into the snip while `include_pointer` is on. `fixed_size`:
        Fixed size mode's frame, in screen pixels. `recordable`: offer the Record switch; `record`:
        start with it on."""
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self._frozen_pixmap = pixmap
        self._frozen_image = pixmap.toImage()
        self._dpr = pixmap.devicePixelRatio() or 1.0
        self._pointer_shot = pointer_shot
        self._include_pointer = include_pointer
        self._with_pointer: tuple[QPixmap, QImage] | None = None
        self.setGeometry(screen.geometry())
        origin = screen.geometry().topLeft()
        self._origin = origin
        self.capture_screen = screen
        self._scrolling = scroll_input.available() if scrolling is None else scrolling
        self._windows = self._to_local(origin, window_rects)
        self._sources = [(local, info) for r, info in zip(window_rects, sources or [], strict=False)
                         if (local := self._map(origin, r)) is not None]
        self.last_source = None  # the window the latest selection came from, if any
        self.last_rect: QRect | None = None  # the latest snip, in this monitor's screen pixels
        self._magnifier = prefs.magnifier()
        self._press: QPoint | None = None
        self._current: QPoint | None = None
        self._dragging = False
        self._cursor: QPoint | None = None
        self._hover: QRect | None = None
        self._fixed_size = tuple(fixed_size)
        self._fixed: QRect | None = None  # Fixed size mode's frame, in screen pixels, once placed
        self.recordable = recordable
        self.record = record and recordable
        self._note: str | None = None  # said in place of the hint for a moment (an area too small)
        self._mode = mode if mode in MODES and (mode != "scroll" or (self._scrolling and not self.record)) \
            else "area"
        self.toolbar: CaptureToolbar | None = None
        if toolbar:
            self.toolbar = CaptureToolbar(self._mode, self, delay=delay, pointer=self._pointer_shown(),
                                          pointer_available=pointer_shot is not None,
                                          pointer_why=_pointer_unavailable_reason(),
                                          scrolling=self._scrolling, fixed_size=self._fixed_size,
                                          recordable=recordable, record=self.record)
            self.toolbar.record_toggled.connect(self._pick_record)
            self.toolbar.mode_picked.connect(self._pick_mode)
            self.toolbar.size_picked.connect(self._pick_size)
            self.toolbar.delay_picked.connect(self.delay_changed)
            self.toolbar.pointer_toggled.connect(self._pick_pointer)
            self.toolbar.cancel_clicked.connect(self.cancelled)
            self.toolbar.installEventFilter(self)
            self._place_toolbar()
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)  # Tab goes round the toolbar and back here
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAccessibleName("Choose what to snip")
        self.setAccessibleDescription(
            "A to drag an area, W for a window, F for the full screen, "
            "X for a fixed size frame (arrow keys move it, Enter snips it, X again picks its size), "
            + ("S to scroll a long page or list, " if self._scrolling else "")
            + "Enter for the whole monitor, D for a delay, P to include the pointer, Esc to cancel")

    # ------------------------------------------------------------------ what's on screen
    def _pointer_shown(self) -> bool:
        return self._include_pointer and self._pointer_shot is not None

    def _pixmap_and_image(self) -> tuple[QPixmap, QImage]:
        """The frozen screen, with the pointer drawn on when it's included."""
        if not self._pointer_shown():
            return self._frozen_pixmap, self._frozen_image
        if self._with_pointer is None:
            image = pointer.composite(self._frozen_image, self._pointer_shot, self._origin)
            pixmap = QPixmap.fromImage(image)
            pixmap.setDevicePixelRatio(self._dpr)
            self._with_pointer = (pixmap, image)
        return self._with_pointer

    @property
    def _pixmap(self) -> QPixmap:
        return self._pixmap_and_image()[0]

    @property
    def _image(self) -> QImage:
        return self._pixmap_and_image()[1]

    @property
    def include_pointer(self) -> bool:
        return self._include_pointer

    def set_include_pointer(self, on: bool) -> None:
        """Without announcing it (the session keeps monitors in step)."""
        self._include_pointer = on
        if self.toolbar is not None:
            self.toolbar.set_pointer(self._pointer_shown())
        self.update()

    def _pick_pointer(self, on: bool) -> None:
        if self._pointer_shot is None:
            return
        self.set_include_pointer(on)
        self.pointer_changed.emit(on)

    def set_delay(self, seconds: int) -> None:
        if self.toolbar is not None:
            self.toolbar.set_delay(seconds)

    # ------------------------------------------------------------------ mode and toolbar
    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Switch modes without announcing it (the session uses this to keep monitors in step)."""
        if mode not in MODES:
            raise ValueError(f"unknown capture mode {mode!r}")
        self._mode = mode
        self._press = self._current = None  # a press or drag that began in the old mode is dropped
        self._dragging = False
        if self.toolbar is not None:
            self.toolbar.show()
        if self.toolbar is not None:
            self.toolbar.set_mode(mode)
            self._place_toolbar()
        self._refresh_hover()
        self.update()

    def set_record(self, on: bool) -> None:
        """Switch Record on or off without announcing it (the session keeps monitors in step)."""
        self.record = on and self.recordable
        if self.record and self._mode == "scroll":
            self.set_mode("area")
        if self.toolbar is not None:
            self.toolbar.set_record(self.record)
            self._place_toolbar()
        self.update()

    def _pick_record(self, on: bool) -> None:
        if self.recordable and on != self.record:
            self.set_record(on)
            self.record_changed.emit(on)
        elif self.toolbar is not None:
            self.toolbar.set_record(self.record)  # a click that changed nothing leaves the button as it was

    def _pick_mode(self, mode: str) -> None:
        if mode == "scroll" and (not self._scrolling or self.record):
            return
        if mode != self._mode:
            self.set_mode(mode)
            if mode == "fixed":
                self._show_frame()  # at once, before the mouse moves
            self.mode_changed.emit(mode)

    def hint(self) -> str:
        if self._note:
            return self._note
        scroll = self._scrolling and not self.record
        letters = " ".join(spec.key for key, spec in MODES.items() if key != "scroll" or scroll)
        if self.record:
            return f"Choose what to record: {MODES[self._mode].hint}  •  {letters} modes  •  R to snip instead"
        record = "  •  R record" if self.recordable else ""
        return f"{MODES[self._mode].hint}  •  {letters} modes  •  D delay  •  P pointer{record}"

    def _refresh_hover(self) -> None:
        """Re-aim the highlight after the mode or the frame's size changed."""
        if self._mode != "fixed":
            self._hover = self._target_at(self._cursor) if self._cursor is not None else None
        elif self._cursor is not None:
            self._set_fixed(self._fixed_at(self._cursor))
        elif self._fixed is not None:  # the pointer is on the toolbar: the frame keeps its middle
            self._set_fixed(self._centred(self._fixed.x() + self._fixed.width() / 2,
                                          self._fixed.y() + self._fixed.height() / 2))
        else:
            self._hover = None

    def _place_toolbar(self) -> None:
        """Centred at the top. It's placed by its Fixed size width, so the size button coming and
        going never moves the other buttons."""
        if self.toolbar is None:
            return
        self.toolbar.set_compact(False)
        if self.toolbar.widest() > self.width() - 2 * TOOLBAR_TOP:  # a narrow monitor: icons only
            self.toolbar.set_compact(True)
        self.toolbar.move((self.width() - self.toolbar.widest()) // 2, TOOLBAR_TOP)

    # ------------------------------------------------------------------ Fixed size
    @property
    def fixed_size(self) -> tuple[int, int]:
        return self._fixed_size

    def set_fixed_size(self, width: int, height: int) -> None:
        """Without announcing it (the session keeps monitors in step)."""
        self._fixed_size = (width, height)
        if self.toolbar is not None:
            self.toolbar.set_fixed_size(width, height)
            self._place_toolbar()
        if self._mode == "fixed":
            self._refresh_hover()
        self.update()

    def _pick_size(self, width: int, height: int) -> None:
        self.set_fixed_size(width, height)
        self.size_changed.emit(width, height)
        # Back to the frame (after the menu hands the keyboard back to the toolbar), so arrow keys
        # move it and Enter snips it.
        QTimer.singleShot(0, self, self.setFocus)

    def _pointer_here(self) -> QPoint | None:
        """Where the pointer is on this monitor (overlay coordinates), or None if it's elsewhere."""
        at = QCursor.pos() - self.geometry().topLeft()
        return at if self.rect().contains(at) else None

    def _show_frame(self) -> bool:
        """Draw the frame if it isn't drawn yet: at the pointer, or in the middle. Returns whether
        it was already showing (Enter never snips a frame you haven't seen)."""
        if self._fixed is not None:
            return True
        at = self._pointer_here()
        if at is None or (self.toolbar is not None and self.toolbar.geometry().contains(at)):
            at = self._middle()  # elsewhere, or picking it on the toolbar: not up under the toolbar
        self._set_fixed(self._fixed_at(at))
        return False

    def pointer_left(self) -> None:
        """The pointer (and the keyboard) went to another monitor: nothing stays aimed here, even
        if a menu kept the overlay from hearing that it left."""
        if self._press is None:
            self._cursor = self._hover = self._fixed = None
            self.update()

    def _middle(self) -> QPoint:
        return QPoint(self.width() // 2, self.height() // 2)

    def _fixed_physical_size(self) -> QSize:
        """The frame in screen pixels: the chosen size, or as much of it as this monitor has."""
        bounds = self._frozen_image.size()
        return QSize(min(self._fixed_size[0], bounds.width()), min(self._fixed_size[1], bounds.height()))

    def _clamped(self, top_left: QPoint) -> QRect:
        """The frame with its corner at `top_left` (screen pixels), kept on this monitor."""
        size, bounds = self._fixed_physical_size(), self._frozen_image.size()
        return QRect(QPoint(min(max(top_left.x(), 0), bounds.width() - size.width()),
                            min(max(top_left.y(), 0), bounds.height() - size.height())), size)

    def _centred(self, x: float, y: float) -> QRect:
        """The frame around (x, y), in screen pixels."""
        size = self._fixed_physical_size()
        return self._clamped(QPoint(round(x - size.width() / 2), round(y - size.height() / 2)))

    def _fixed_at(self, pos: QPoint) -> QRect:
        """The frame around the pointer at `pos` (overlay coordinates), in screen pixels."""
        return self._centred(pos.x() * self._dpr, pos.y() * self._dpr)

    def _set_fixed(self, frame: QRect) -> None:
        self._fixed = frame
        self._hover = self._to_logical(frame)
        self.update()

    def _nudge(self, dx: int, dy: int) -> None:
        self._show_frame()
        self._set_fixed(self._clamped(self._fixed.topLeft() + QPoint(dx, dy)))

    def _snip_fixed(self) -> None:
        if self._show_frame():
            self._emit_physical(self._fixed, self._to_logical(self._fixed))

    def showEvent(self, e) -> None:  # noqa: N802 - Qt API
        super().showEvent(e)
        if self._mode == "fixed" and self._fixed is None and (at := self._pointer_here()) is not None:
            self._set_fixed(self._fixed_at(at))  # there from the start, under the pointer

    def enterEvent(self, e) -> None:  # noqa: N802 - Qt API
        """The keyboard follows the pointer from monitor to monitor, so arrow keys and Enter act on
        the monitor you're looking at, not the one the snip started on."""
        if QApplication.activePopupWidget() is None and not self.isActiveWindow():
            self.activateWindow()
            self.setFocus()
            self.keyboard_taken.emit()
        super().enterEvent(e)

    def resizeEvent(self, e) -> None:  # noqa: N802 - Qt API
        self._place_toolbar()
        super().resizeEvent(e)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if watched is self.toolbar and event.type() == QEvent.Enter:
            self._cursor = None  # no magnifier or window highlight behind the toolbar
            if self._press is None and self._mode != "fixed":  # the frame stays, to see a new size
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
        """The window a snip came from (capture_area.source_for)."""
        return source_for(self._sources, sel)

    # ------------------------------------------------------------------ geometry
    def _selection(self) -> QRect | None:
        if self._dragging and self._press is not None and self._current is not None:
            return span(self._press, self._current).intersected(self.rect())
        return self._hover

    def _target_at(self, pos: QPoint) -> QRect | None:
        """What a click at `pos` takes in the current mode."""
        if self._mode == "screen":
            return self.rect()
        if self._mode == "fixed":
            return self._to_logical(self._fixed_at(pos))
        # The last entry is the whole monitor, offered when Area mode hovers the desktop.
        windows = self._windows if self._mode in DRAG_MODES else self._windows[:-1]
        return next((r for r in windows if r.contains(pos)), None)

    def _to_physical(self, r: QRect) -> QRect:
        return QRect(round(r.x() * self._dpr), round(r.y() * self._dpr),
                     round(r.width() * self._dpr), round(r.height() * self._dpr))

    def _to_logical(self, r: QRect) -> QRect:
        return QRect(round(r.x() / self._dpr), round(r.y() / self._dpr),
                     round(r.width() / self._dpr), round(r.height() / self._dpr))

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
            paint_size_label(p, sel, self._size_text(sel))
        if not self._dragging and self._press is None:
            paint_hint(p, self.size(), self.hint())
        if self._cursor is not None and self._shows_magnifier():
            paint_magnifier(p, self._image, self._dpr, self._cursor, self.size())

    def _shows_magnifier(self) -> bool:
        return self._magnifier and self._mode != "fixed"  # the frame's middle isn't what you're lining up

    def _outside(self, sel: QRect) -> list[QRect]:
        r = self.rect()
        return [
            QRect(r.left(), r.top(), r.width(), sel.top() - r.top()),
            QRect(r.left(), sel.bottom() + 1, r.width(), r.bottom() - sel.bottom()),
            QRect(r.left(), sel.top(), sel.left() - r.left(), sel.height()),
            QRect(sel.right() + 1, sel.top(), r.right() - sel.right(), sel.height()),
        ]

    def _size_text(self, sel: QRect) -> str:
        """The snip's size in screen pixels (Fixed size: exactly the frame's)."""
        if self._mode == "fixed":
            size = self._fixed_physical_size()
            return f"{size.width()} × {size.height()}"
        return f"{round(sel.width() * self._dpr)} × {round(sel.height() * self._dpr)}"

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
        if self._mode == "fixed":  # the frame follows the pointer, pressed or not
            self._set_fixed(self._fixed_at(pos))
        elif self._press is not None:
            self._current = pos
            if self._mode in DRAG_MODES and (pos - self._press).manhattanLength() >= DRAG_THRESHOLD:
                self._dragging = True
                if self.toolbar is not None:
                    self.toolbar.hide()  # out of the way of the area being chosen
        else:
            self._hover = self._target_at(pos)
        self.update()

    def leaveEvent(self, _e) -> None:
        self._cursor = None
        if self._press is None and QApplication.activePopupWidget() is None:  # a menu isn't leaving
            self._hover = None
            self._fixed = None  # the pointer went to another monitor: so does the frame
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.LeftButton or self._press is None:
            return
        if self._mode == "fixed":
            self._press = self._current = None
            self._set_fixed(self._fixed_at(e.position().toPoint()))
            self._snip_fixed()
            return
        sel = span(self._press, e.position().toPoint()).intersected(self.rect()) \
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
            if self._mode == "fixed":
                self._snip_fixed()
            else:
                self._emit(self.rect())
        elif self._mode == "fixed" and e.key() in ARROWS and (step := _nudge_step(e.modifiers())):
            dx, dy = ARROWS[e.key()]
            self._nudge(dx * step, dy * step)
        elif e.modifiers() not in (Qt.NoModifier, Qt.ShiftModifier) or e.isAutoRepeat():
            super().keyPressEvent(e)  # a held letter (e.g. the S of Win+Shift+S) isn't a choice
        elif mode := next((k for k, spec in MODES.items() if e.key() == ord(spec.key)), None):
            if mode == "fixed" == self._mode and self.toolbar is not None:
                self.toolbar.open_size_menu()  # X again: the size
            else:
                self._pick_mode(mode)
        elif e.key() == Qt.Key_R and self.recordable and self._press is None:
            self._pick_record(not self.record)
        elif e.key() == Qt.Key_D and self.toolbar is not None and not self.record:
            if self._press is None:  # not in the middle of a drag (the toolbar is hidden then)
                self.toolbar.open_delay_menu()
        elif e.key() == Qt.Key_P and not self.record:
            self._pick_pointer(not self._include_pointer)
        else:
            super().keyPressEvent(e)

    def _emit(self, sel: QRect) -> None:
        if self._mode == "scroll":
            self.scroll_requested.emit(QRect(sel))
            return
        self._emit_physical(self._to_physical(sel), sel)

    def _emit_physical(self, physical: QRect, sel: QRect) -> None:
        """Snip `physical` (screen pixels); `sel` is the same area in overlay coordinates. With Record
        on, it's the area to record instead, in physical desktop pixels (Qt keeps each monitor's
        top-left where Windows has it)."""
        if self.record:
            self._choose_to_record(physical)
            return
        image = self._image.copy(physical)
        image.setDevicePixelRatio(1.0)
        self.last_rect = QRect(physical)
        self.last_source = self._source_for(sel)
        self.selected.emit(image)

    def _choose_to_record(self, physical: QRect) -> None:
        scale = min(1.0, 1920 / physical.width(), 1080 / physical.height())  # recorded at most 1080p
        if min(physical.width(), physical.height()) * scale < MIN_RECORD_SIDE:
            self._say(f"That's too small to record: choose at least {MIN_RECORD_SIDE} × {MIN_RECORD_SIDE} pixels")
            return
        self.last_rect = QRect(physical)
        self.area_chosen.emit(physical.translated(self._origin))

    def _say(self, note: str) -> None:
        """Show `note` where the hint goes, for a few seconds."""
        self._note = note
        self.update()
        QTimer.singleShot(NOTE_MS, self, lambda: (setattr(self, "_note", None), self.update()))


def _nudge_step(modifiers) -> int:
    """How far an arrow key moves the frame: 0 when Ctrl or Alt is held (not a nudge)."""
    modifiers = modifiers & ~Qt.KeypadModifier  # the number pad's arrows nudge too
    if modifiers == Qt.NoModifier:
        return FIXED_NUDGE
    return FIXED_NUDGE_SHIFT if modifiers == Qt.ShiftModifier else 0


class CaptureSession:
    """Owns the overlays for one capture; calls back with the image or None."""

    def __init__(self, on_done, repeat: prefs.LastArea | None = None, on_note=None, on_area=None,
                 record: bool = False) -> None:
        """`repeat`: Repeat last snip, this area taken again with no overlay. `on_note(text)` tells
        the person something straight away (e.g. that the area to repeat has gone). `on_area(rect,
        screen)` offers the Record switch, and gets the area chosen to record (physical desktop
        pixels); `record` starts with it on (and without a snip's delay)."""
        self._on_done = on_done
        self._on_area = on_area
        self.record = record and on_area is not None
        self._repeat = repeat
        self._on_note = on_note
        self.area: prefs.LastArea | None = None  # where the snip came from, for the next repeat
        self._overlays: list[_ScreenOverlay] = []
        self._finished = False
        self.focus: _ScreenOverlay | None = None  # the overlay that has the keyboard
        self._mode: str | None = None  # the mode picked in this snip (kept across a delay's re-freeze)
        self.countdown: Countdown | None = None  # counting down before the screen freezes
        self.scrolling: ScrollCapture | None = None  # Scrolling mode, under way
        self.note: str | None = None  # why a scrolling capture ended early, for the person
        self.source = None  # the window the snip came from (winrects.WindowInfo), when known

    def start(self) -> None:
        delay = 0 if self.record else prefs.capture_delay()  # a recording counts down after the area
        if delay:
            self._count_down(delay)
        else:
            self._freeze()

    def snip_now(self) -> None:
        """The snip key pressed again: skip the rest of a countdown, or stop scrolling and keep
        what's been captured."""
        if self.countdown is not None:
            self.countdown.snip_now()
        elif self.scrolling is not None:
            self.scrolling.stop()

    def cancel(self) -> None:
        """Stop this snip, whatever stage it's at (the tray's Cancel snip)."""
        self._finish(None)

    def _count_down(self, seconds: int) -> None:
        self._drop_countdown()
        try:
            screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            self.countdown = Countdown(seconds, screen)
            self.countdown.finished.connect(self._countdown_done)
            self.countdown.cancelled.connect(lambda: self._finish(None))
            self.countdown.start()
        except Exception:  # never leave a session that blocks every later snip
            logging.getLogger(__name__).exception("The snip countdown failed to start")
            self._finish(None)

    def _countdown_done(self) -> None:
        self._drop_countdown()
        if not self._finished:
            self._freeze()

    def _drop_countdown(self) -> None:
        if self.countdown is not None:
            self.countdown.blockSignals(True)  # nothing from it arrives after this
            self.countdown.close()
            self.countdown.deleteLater()
            self.countdown = None

    def _freeze(self) -> None:
        try:
            if self._repeat is not None:
                self._take_again()
            else:
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
        mode = self._mode or prefs.capture_mode()
        if mode == "scroll" and self._mode is None:  # remembered by an older version: never start in it
            mode = "area"
        shot = pointer.grab()  # first: the pointer as it is the moment the screen freezes
        # Every monitor gets the toolbar; the keyboard goes to the one the pointer is on (the main
        # one if the pointer is somewhere Windows doesn't report, e.g. while a remote session reconnects).
        active = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        for screen in QGuiApplication.screens():
            overlay = _ScreenOverlay(screen, _grab_screen(screen), windows, infos, mode=mode, toolbar=True,
                                     delay=prefs.capture_delay(), pointer_shot=shot,
                                     include_pointer=prefs.capture_pointer(),
                                     scrolling=scroll_input.available(), fixed_size=prefs.capture_fixed_size(),
                                     record=self.record, recordable=self._on_area is not None)
            overlay.selected.connect(lambda image, o=overlay: self._took(o, image))
            overlay.area_chosen.connect(lambda rect, o=overlay: self._chose_to_record(o, rect))
            overlay.record_changed.connect(self._set_record)
            overlay.cancelled.connect(lambda: self._finish(None))
            overlay.mode_changed.connect(self._set_mode)
            overlay.delay_changed.connect(self._set_delay)
            overlay.pointer_changed.connect(self._set_pointer)
            overlay.size_changed.connect(self._set_fixed_size)
            overlay.keyboard_taken.connect(lambda o=overlay: self._keyboard_to(o))
            overlay.scroll_requested.connect(lambda rect, o=overlay: self._scroll(o, rect))
            self._overlays.append(overlay)
            if screen is active:
                self.focus = overlay
        for overlay in self._overlays:
            overlay.showFullScreen()
            overlay.raise_()
        if self.focus is not None:
            self.focus.activateWindow()
            self.focus.setFocus()

    def _set_record(self, on: bool) -> None:
        """Record switched on or off on one monitor applies to all of them."""
        self.record = on
        for overlay in self._overlays:
            overlay.set_record(on)

    def _chose_to_record(self, overlay: _ScreenOverlay, rect: QRect) -> None:
        """The area to record: this session is over, and the recording (snipforge/video) takes it."""
        screen = overlay.capture_screen
        self._finish(None)
        self._on_area(QRect(rect), screen)

    def _set_mode(self, mode: str) -> None:
        """A mode picked on one monitor applies to all of them, and to the next snip, except
        Scrolling: it acts on whatever is under the pointer, so it's chosen afresh every time."""
        self._mode = mode
        for overlay in self._overlays:
            overlay.set_mode(mode)
        if mode != "scroll":
            prefs.set_capture_mode(mode)

    def _set_delay(self, seconds: int) -> None:
        """Remembered for the next snip. A delay also starts now: the overlays step aside, the
        countdown runs, and the screen freezes again (with the menu you opened meanwhile)."""
        prefs.set_capture_delay(seconds)
        for overlay in self._overlays:
            overlay.set_delay(seconds)
        if seconds and not self._finished:
            self._close_overlays()
            self._count_down(seconds)

    def _keyboard_to(self, overlay: _ScreenOverlay) -> None:
        """The pointer came to `overlay`'s monitor: it has the keyboard now, and the others let go."""
        self.focus = overlay
        for other in self._overlays:
            if other is not overlay:
                other.pointer_left()

    def _set_fixed_size(self, width: int, height: int) -> None:
        """A size picked on one monitor applies to all of them, and to the next snip."""
        prefs.set_capture_fixed_size(width, height)
        for overlay in self._overlays:
            overlay.set_fixed_size(width, height)

    def _scroll(self, overlay: _ScreenOverlay, rect: QRect) -> None:
        """Scrolling mode: the overlays step aside and the chosen area is scrolled and stitched."""
        physical = overlay._to_physical(rect)
        source = overlay._source_for(rect)
        screen = overlay.capture_screen
        self._close_overlays()
        try:
            self.scrolling = ScrollCapture(screen, physical)
            self.scrolling.finished.connect(lambda image: self._scroll_done(image, source))
            self.scrolling.start()
        except Exception:  # never leave a session that blocks every later snip
            logging.getLogger(__name__).exception("Scrolling capture failed to start")
            self.note = "Scrolling capture couldn't start. Try again, or snip the area instead."
            self._finish(None)

    def _took(self, overlay: _ScreenOverlay, image: QImage) -> None:
        """A snip chosen on `overlay` (or repeated there): remember where, for Repeat last snip."""
        if overlay.last_rect is not None:
            self.area = area_on(overlay.capture_screen, overlay.last_rect)
        self._finish(image, overlay.last_source)

    def _take_again(self) -> None:
        """Repeat last snip: the same area of the same monitor, straight away. It goes through an
        overlay that's never shown, so the pointer, the window it came from and the image come out
        exactly as a chosen snip's would. An area that's no longer on screen gets a normal snip."""
        area, self._repeat = self._repeat, None
        rect = QRect(*area.rect)
        screen = screen_for(area)
        shot = pointer.grab() if screen is not None else None  # first, as for any snip
        pixmap = _grab_screen(screen) if screen is not None else None
        if pixmap is None or not QRect(QPoint(0, 0), pixmap.size()).contains(rect):
            gone = "Your last snip's area isn't on screen any more, so choose what to snip."
            if self._on_note is not None:
                self._on_note(gone)  # now, while choosing, not after
            else:
                self.note = gone
            self._show_overlays()
            return
        try:
            from .winrects import window_infos
            infos = window_infos()
        except OSError:
            infos = []
        overlay = _ScreenOverlay(screen, pixmap, [info.rect for info in infos], infos, pointer_shot=shot,
                                 include_pointer=prefs.capture_pointer(), scrolling=False)
        try:
            overlay.selected.connect(lambda image: self._took(overlay, image))
            overlay._emit_physical(rect, overlay._to_logical(rect))
        finally:
            overlay.deleteLater()

    def _scroll_done(self, image, source) -> None:
        self.note = getattr(self.scrolling, "note", None)
        self._finish(image, source)

    def _set_pointer(self, on: bool) -> None:
        prefs.set_capture_pointer(on)
        for overlay in self._overlays:
            overlay.set_include_pointer(on)

    def _close_overlays(self) -> None:
        for overlay in self._overlays:
            overlay.close()
        self._overlays.clear()
        self.focus = None

    def _finish(self, image: QImage | None, source=None) -> None:
        if self._finished:
            return
        self._finished = True
        self.source = source if image is not None else None
        self._drop_countdown()
        if self.scrolling is not None:
            self.scrolling.blockSignals(True)  # it already reported, or is being cancelled now
            self.scrolling.cancel()
            self.scrolling = None
        self._close_overlays()
        self._on_done(image)
