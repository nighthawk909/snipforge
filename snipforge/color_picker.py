"""Pick a color from anywhere on screen: the tray's Pick a color, or a shortcut of your own.

Every monitor freezes, as for a snip (capture.py), and shows its screen as it was, not dimmed, so
colors look as they are. A loupe beside the pointer enlarges the screen pixels around it, with the
one you'd pick outlined and its code underneath. Picks are exact screen pixels at any display
scale, monitor by monitor (each overlay works in its own monitor's pixels). The arrow keys move a
screen pixel at a time (Shift: 10), which reaches pixels the mouse can't land on at 125% or 150%. A
click, Enter or Space picks; Tab changes what's copied (HEX, RGB or HSL); Esc or a right-click
cancels.

The color picked is copied, added to the editor's recent colors (editor/colors.py), and announced.
"""
from __future__ import annotations

import contextlib
import logging

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from . import color_text, loupe, prefs, scroll_input
from .capture import ARROWS, _nudge_step  # the arrow keys move a pixel, Shift 10, as for Fixed size
from .capture_paint import HINT_BOTTOM, paint_hint
from .editor import colors
from .fonts import mono_font

log = logging.getLogger(__name__)

START_DELAY_MS = 120  # as for a snip: let the tray menu close and the shortcut's keys come up first
GAP = 20  # between the pixel and the loupe
HINT_BAND = HINT_BOTTOM + 40  # the hint's place at the bottom, which it leaves to the loupe
PICK_KEYS = (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space)
COULD_NOT_START = "Pick a color couldn't start. Try again."


def _grab_screen(screen) -> QPixmap:
    return screen.grabWindow(0)


def _pointer() -> QPoint:
    """Where the pointer is, in physical screen pixels: Qt's own position is in scaled pixels, rounded,
    which can be a pixel off at 125% or 150%."""
    if scroll_input.available():
        try:
            return scroll_input.pointer_position()
        except OSError:
            log.warning("Couldn't read the pointer's position", exc_info=True)
    return QCursor.pos()


class PickOverlay(QWidget):
    """One monitor, frozen, with the loupe. Positions it keeps (`point`) are this monitor's screen
    pixels, from its top-left."""

    picked = Signal(QColor)
    cancelled = Signal()
    format_changed = Signal(str)  # Tab chose what's copied here
    keyboard_taken = Signal()  # the pointer came to this monitor, and the keyboard came with it

    RADIUS = 7  # screen pixels on each side of the one you'd pick: a 15 x 15 sample
    CELL = 9  # on-screen size of one of them

    def __init__(self, screen, pixmap: QPixmap, copy_as: str = "hex") -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self._pixmap = pixmap
        self._image = pixmap.toImage()
        self._dpr = pixmap.devicePixelRatio() or 1.0
        self.capture_screen = screen
        self.copy_as = copy_as if copy_as in color_text.FORMATS else color_text.FORMATS[0]
        self._point: QPoint | None = None
        self._pressed = False  # a left press began here (a release alone picks nothing)
        self._hint_shown = True
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAccessibleName("Pick a color")
        self.setAccessibleDescription(
            "Move the pointer to the color you want and click to copy its code. Or use the arrow keys to "
            "move a pixel at a time (Shift: 10), and Enter or Space to copy it. Tab changes what's copied: "
            "HEX, RGB or HSL. Esc cancels.")

    # ------------------------------------------------------------------ what's shown
    def point(self) -> QPoint | None:
        """The screen pixel the loupe shows, or None before the pointer or a key moved it."""
        return QPoint(self._point) if self._point is not None else None

    def color(self) -> QColor | None:
        if self._point is None:
            return None
        color = self._image.pixelColor(self._point)
        return QColor(color.red(), color.green(), color.blue())

    def label(self) -> str:
        """The color the loupe shows, written as it would be copied."""
        color = self.color()
        return color_text.as_text(color, self.copy_as) if color is not None else ""

    def hint(self) -> str:
        now, then = (color_text.LABELS[f] for f in (self.copy_as, color_text.next_format(self.copy_as)))
        return f"Click to copy a color as {now}  •  Tab: {then}  •  Arrow keys move a pixel  •  Esc cancels"

    def loupe_rect(self) -> QRect | None:
        """Where the loupe is (overlay coordinates): beside the pixel, on whichever side keeps it on
        this monitor."""
        if self._point is None:
            return None
        box = loupe.size(self.RADIUS, self.CELL)
        at = self._logical(self._point)
        x, y = at.x() + GAP, at.y() + GAP
        if x + box.width() > self.width():
            x = at.x() - GAP - box.width()
        if y + box.height() > self.height():
            y = at.y() - GAP - box.height()
        x = min(max(x, 0), self.width() - box.width())
        y = min(max(y, 0), self.height() - box.height())
        return QRect(round(x), round(y), box.width(), box.height())

    def set_copy_as(self, fmt: str) -> None:
        """Without announcing it (the session keeps monitors in step)."""
        self.copy_as = fmt
        self.update()

    def pointer_left(self) -> None:
        """The pointer (and the keyboard) went to another monitor: nothing stays aimed here."""
        if self._point is not None:
            area = self._loupe_area()
            self._point = None
            self.update(area)

    # ------------------------------------------------------------------ screen pixels
    def _pixel_at(self, pos: QPointF) -> QPoint:
        """The screen pixel under `pos` (overlay coordinates). Windows reports the pointer in whole
        screen pixels and Qt divides them by the display scale, so multiplying back and rounding
        gets that pixel exactly, even at 125% or 150%."""
        return self._clamped(QPoint(round(pos.x() * self._dpr), round(pos.y() * self._dpr)))

    def _clamped(self, pixel: QPoint) -> QPoint:
        return QPoint(min(max(pixel.x(), 0), self._image.width() - 1),
                      min(max(pixel.y(), 0), self._image.height() - 1))

    def _logical(self, pixel: QPoint) -> QPointF:
        """The middle of a screen pixel, in overlay coordinates."""
        return QPointF((pixel.x() + 0.5) / self._dpr, (pixel.y() + 0.5) / self._dpr)

    def _start_point(self) -> QPoint:
        """Where the keyboard starts: the pixel under the pointer, if it's on this monitor, else the
        middle. (Qt keeps each monitor's top-left where Windows has it, in physical pixels.)"""
        at = _pointer() - self.geometry().topLeft()
        if self._image.rect().contains(at):
            return at
        return QPoint(self._image.width() // 2, self._image.height() // 2)

    # ------------------------------------------------------------------ painting
    def _loupe_area(self) -> QRect:
        box = self.loupe_rect()
        return box.adjusted(-2, -2, 2, 2) if box is not None else QRect()

    def _hint_fits(self) -> bool:
        """Whether the hint shows: not while the pixel or the loupe is where it goes."""
        if self._point is None:
            return True
        top = self.height() - HINT_BAND
        return self._logical(self._point).y() < top and self.loupe_rect().bottom() < top

    def _show(self, pixel: QPoint) -> None:
        """Aim at `pixel`, repainting only the loupe (old and new), not the whole monitor."""
        old = self._loupe_area()
        self._point = pixel
        hint = self._hint_fits()
        if hint != self._hint_shown:
            self._hint_shown = hint
            self.update()
            return
        self.update(old)
        self.update(self._loupe_area())

    def paintEvent(self, _e) -> None:  # noqa: N802 - Qt API
        p = QPainter(self)
        p.drawPixmap(0, 0, self._pixmap)
        if self._hint_shown:
            paint_hint(p, self.size(), self.hint())
        box = self.loupe_rect()
        if box is not None:
            font = mono_font(8)  # hsl(359, 100%, 50%) fits beside the swatch
            font.setBold(True)
            pixels = loupe.sample(self._image, self._point.x(), self._point.y(), self.RADIUS)
            loupe.paint(p, box.topLeft(), pixels, self.label(), radius=self.RADIUS, cell=self.CELL,
                        ratio=self.devicePixelRatioF() or 1.0, font=font, grid=True, swatch=self.color())
        p.end()

    # ------------------------------------------------------------------ input
    def enterEvent(self, e) -> None:  # noqa: N802 - Qt API
        """The keyboard follows the pointer from monitor to monitor."""
        if not self.isActiveWindow():
            self.activateWindow()
            self.setFocus()
            self.keyboard_taken.emit()
        super().enterEvent(e)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802 - Qt API
        self._show(self._pixel_at(e.position()))

    def mousePressEvent(self, e) -> None:  # noqa: N802 - Qt API
        if e.button() == Qt.LeftButton:
            self._pressed = True
            self._show(self._pixel_at(e.position()))

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802 - Qt API
        """Picks (and cancels) on release, so the release never reaches the window underneath, where a
        right-button release would open its menu."""
        if e.button() == Qt.RightButton:
            self.cancelled.emit()
        elif e.button() == Qt.LeftButton and self._pressed:
            self._pressed = False
            if not self.rect().contains(e.position().toPoint()):
                return  # let go on another monitor: this one's edge isn't what you were pointing at
            self._show(self._pixel_at(e.position()))
            self._pick()

    def closeEvent(self, e) -> None:  # noqa: N802 - Qt API
        """Closed some other way (Alt+F4) ends the whole pick, so no monitor stays frozen."""
        super().closeEvent(e)
        self.cancelled.emit()

    def focusNextPrevChild(self, _next: bool) -> bool:  # noqa: N802 - Qt API
        return False  # Tab changes what's copied (keyPressEvent): there's nothing else to go to

    def keyPressEvent(self, e) -> None:  # noqa: N802 - Qt API
        key, plain = e.key(), not e.modifiers() & (Qt.ControlModifier | Qt.AltModifier)
        if key == Qt.Key_Escape:
            self.cancelled.emit()
        elif key in PICK_KEYS and plain:
            if e.isAutoRepeat():
                return  # a held Enter picks once
            if self._point is None:
                self._show(self._start_point())  # show where it would pick, first
            else:
                self._pick()
        elif key in ARROWS and (step := _nudge_step(e.modifiers())):
            start = self._point if self._point is not None else self._start_point()
            dx, dy = ARROWS[key]
            self._show(self._clamped(start + QPoint(dx * step, dy * step)))
        elif key in (Qt.Key_Tab, Qt.Key_Backtab) and plain:
            back = key == Qt.Key_Backtab or bool(e.modifiers() & Qt.ShiftModifier)
            self.set_copy_as(color_text.next_format(self.copy_as, -1 if back else 1))
            self.format_changed.emit(self.copy_as)
        else:
            super().keyPressEvent(e)

    def _pick(self) -> None:
        color = self.color()
        if color is not None:
            self.picked.emit(color)


class ColorPickSession:
    """Owns the overlays for one pick, one per monitor; calls back with the color, or None."""

    def __init__(self, on_done) -> None:
        self._on_done = on_done
        self.overlays: list[PickOverlay] = []
        self.focus: PickOverlay | None = None  # the overlay that has the keyboard
        self.failed = False  # it couldn't start (rather than being cancelled)
        self._finished = False

    def start(self) -> None:
        if self._finished:
            return  # cancelled before it started (a snip began meanwhile)
        try:
            self._show_overlays()
        except Exception:  # never leave overlays up that nothing can close
            log.exception("Pick a color failed to start")
            self.failed = True
            self._finish(None)
            return
        if not self.overlays:
            self.failed = True
            self._finish(None)

    def cancel(self) -> None:
        self._finish(None)

    def _show_overlays(self) -> None:
        copy_as = prefs.color_format()
        active = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        for screen in QGuiApplication.screens():
            pixmap = _grab_screen(screen)
            if pixmap.isNull():
                log.warning("Couldn't freeze the monitor %s to pick a color", screen.name())
                continue
            overlay = PickOverlay(screen, pixmap, copy_as=copy_as)
            overlay.picked.connect(self._finish)
            overlay.cancelled.connect(self.cancel)
            overlay.format_changed.connect(self._set_format)
            overlay.keyboard_taken.connect(lambda o=overlay: self._keyboard_to(o))
            self.overlays.append(overlay)
            if screen is active and self.focus is None:
                self.focus = overlay
        if self.focus is None and self.overlays:
            self.focus = self.overlays[0]
        for overlay in self.overlays:
            overlay.showFullScreen()
            overlay.raise_()
        if self.focus is not None:
            self.focus.activateWindow()
            self.focus.setFocus()

    def _set_format(self, fmt: str) -> None:
        """Chosen on one monitor: every monitor, and the next pick, copies that way."""
        prefs.set_color_format(fmt)
        for overlay in self.overlays:
            overlay.set_copy_as(fmt)

    def _keyboard_to(self, overlay: PickOverlay) -> None:
        self.focus = overlay
        for other in self.overlays:
            if other is not overlay:
                other.pointer_left()

    def _finish(self, color: QColor | None) -> None:
        if self._finished:
            return
        self._finished = True
        try:
            for overlay in self.overlays:
                with contextlib.suppress(RuntimeError):  # one Windows already destroyed
                    overlay.close()
        finally:
            self.overlays.clear()
            self.focus = None
            self._on_done(color)  # whatever happened, so the next pick isn't blocked


class ColorPicker:
    """Pick a color, and what happens to it: copied in the format chosen, added to the editor's
    recent colors, and announced with `notify(text)`. One pick at a time. `delay_ms`: how long the
    screen is left to settle before it freezes."""

    def __init__(self, notify, delay_ms: int = START_DELAY_MS) -> None:
        self._notify = notify
        self._delay_ms = delay_ms
        self.session: ColorPickSession | None = None

    def start(self) -> None:
        if self.session is not None:
            return  # already picking, or about to (the shortcut pressed again)
        self.session = session = ColorPickSession(self._done)
        if self._delay_ms:
            QTimer.singleShot(self._delay_ms, session.start)
        else:
            session.start()

    def cancel(self) -> None:
        if self.session is not None:
            self.session.cancel()

    def _done(self, color: QColor | None) -> None:
        session, self.session = self.session, None
        if color is None:
            if session is not None and session.failed:
                self._notify(COULD_NOT_START)
            return
        text = color_text.as_text(color, prefs.color_format())
        QGuiApplication.clipboard().setText(text)
        colors.remember(color.name())
        self._notify(f"Copied {text}\n{color_text.summary(color, copied=prefs.color_format())}")
