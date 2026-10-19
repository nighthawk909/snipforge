"""The editor's timeline: thumbnails along the video, in and out handles for the trim, the parts
cut out of the middle (hatched), a part chosen to cut, and the playhead. Everything snaps to frames.

The strip holds the trim range, the choice and the playhead and says when the person changes them
(range_edited, selection_changed, playhead_edited); the window's own changes go through set_range,
set_cuts, set_selection and set_playhead, which don't signal. Keys: Left/Right move the chosen
handle or the playhead one frame (Shift: ten), Home/End jump to the range's ends, I and O set the
start and end at the playhead, [ and ] the choice's, Esc forgets the choice, Space plays. Shift+drag
chooses a part with the mouse.
"""
from __future__ import annotations

import bisect

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QKeyEvent, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import theme
from .timeline import FrameTimes, TrimRange

HANDLE_W = 12
PAD_Y = 8
BIG_STEP = 10  # frames, with Shift
DIM = QColor(0, 0, 0, 150)  # over the parts the trim leaves out
CUT = QColor(0, 0, 0, 170)  # over the parts cut out of the middle, with hatching
HELP = ("Drag the handles to choose what to keep. Left and Right move the chosen handle or the playhead one "
        "frame (Shift: ten); I and O set the start and end at the playhead; Space plays. To cut out a part, "
        "Shift+drag across it (or [ and ] at the playhead), then X.")


class TimelineStrip(QWidget):
    range_edited = Signal(object)  # TrimRange
    selection_changed = Signal(object)  # (first, stop) or None
    playhead_edited = Signal(int)  # frame index
    play_toggled = Signal()

    def __init__(self, frames: FrameTimes, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.frames = frames
        self.range = TrimRange.whole(frames)
        self.cuts: tuple[tuple[int, int], ...] = ()
        self.selection: tuple[int, int] | None = None  # frames first up to stop, chosen to cut
        self._anchor: int | None = None  # where a Shift+drag choosing a part began (a frame boundary)
        self.playhead = 0
        self.active = "playhead"  # what the arrow keys move: "playhead", "in" or "out"
        self.thumbnails: dict[float, QImage] = {}
        self._thumb_times: list[float] = []
        self._drag: tuple[str, float] | None = None  # what's being dragged, and where it was grabbed
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName("Timeline")
        self.setToolTip(HELP)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(800, 76)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(240, 76)

    # ------------------------------------------------------------------ state
    def set_range(self, trim_range: TrimRange) -> None:
        self.range = trim_range
        self._changed()

    def set_cuts(self, cuts: tuple[tuple[int, int], ...]) -> None:
        self.cuts = tuple(cuts)
        self._changed()

    def set_selection(self, selection: tuple[int, int] | None) -> None:
        self.selection = selection
        self._changed()

    def _edit_selection(self, selection: tuple[int, int] | None) -> None:
        if selection is not None:
            first, stop = sorted(selection)
            selection = (max(0, first), min(len(self.frames), max(stop, first + 1)))
        if selection != self.selection:
            self.set_selection(selection)
            self.selection_changed.emit(selection)

    def set_playhead(self, index: int) -> None:
        self.playhead = max(0, min(index, len(self.frames) - 1))
        self._changed()

    def add_thumbnail(self, seconds: float, image: QImage) -> None:
        self.thumbnails[seconds] = image
        self._thumb_times = sorted(self.thumbnails)
        self.update()

    def _changed(self) -> None:
        parts = len(self.cuts)
        cut = f"; {parts} part{'' if parts == 1 else 's'} cut out" if parts else ""
        chosen = f"; frames {self.selection[0] + 1} to {self.selection[1]} chosen to cut" if self.selection else ""
        self.setAccessibleDescription(f"Keeping frames {self.range.first + 1} to {self.range.stop} of "
                                      f"{len(self.frames)}{cut}{chosen}; playhead on frame {self.playhead + 1}")
        self.update()

    def _edit_range(self, trim_range: TrimRange, show: int) -> None:
        """A change the person made: the range, and the frame to show for it."""
        if trim_range != self.range:
            self.set_range(trim_range)
            self.range_edited.emit(trim_range)
        self._edit_playhead(show)

    def _edit_playhead(self, index: int) -> None:
        index = max(0, min(index, len(self.frames) - 1))
        if index != self.playhead:
            self.set_playhead(index)
            self.playhead_edited.emit(index)

    # ------------------------------------------------------------------ geometry
    def track_rect(self) -> QRectF:
        return QRectF(HANDLE_W, PAD_Y, max(1, self.width() - 2 * HANDLE_W), max(1, self.height() - 2 * PAD_Y))

    def x_for(self, seconds: float) -> float:
        track = self.track_rect()
        return track.left() + track.width() * max(0.0, min(seconds, self.frames.end)) / self.frames.end

    def seconds_at(self, x: float) -> float:
        track = self.track_rect()
        return (x - track.left()) / track.width() * self.frames.end

    def handle_rect(self, name: str) -> QRectF:
        top, height = PAD_Y / 2, self.height() - PAD_Y
        if name == "in":
            return QRectF(self.x_for(self.range.start) - HANDLE_W, top, HANDLE_W, height)
        return QRectF(self.x_for(self.range.end), top, HANDLE_W, height)

    def handle_at(self, point: QPointF) -> str | None:
        return next((name for name in ("in", "out") if self.handle_rect(name).contains(point)), None)

    # ------------------------------------------------------------------ mouse
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        point = event.position()
        name = self.handle_at(point)
        if event.modifiers() & Qt.ShiftModifier and not name:  # choose a part to cut out
            self._anchor = self.frames.nearest_boundary(self.seconds_at(point.x()))
            self._drag, self.active = ("choose", 0.0), "playhead"
            self._edit_selection(None)
            return
        if name:
            edge = self.range.start if name == "in" else self.range.end
            self._drag, self.active = (name, point.x() - self.x_for(edge)), name
            self.update()
        else:
            self._drag, self.active = ("playhead", 0.0), "playhead"
            self._edit_playhead(self.frames.index_at(self.seconds_at(point.x())))
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        point = event.position()
        if self._drag is None:
            self.setCursor(Qt.SizeHorCursor if self.handle_at(point) else Qt.ArrowCursor)
            return
        name, grabbed_at = self._drag
        seconds = self.seconds_at(point.x() - grabbed_at)
        if name == "choose":
            here = self.frames.nearest_boundary(seconds)
            if here != self._anchor:
                self._edit_selection((self._anchor, here))
                self._edit_playhead(min(self._anchor, here))
            return
        if name == "in":
            moved = self.range.move_in_to(seconds)
            self._edit_range(moved, moved.first)
        elif name == "out":
            moved = self.range.move_out_to(seconds)
            self._edit_range(moved, moved.last)
        else:
            self._edit_playhead(self.frames.index_at(seconds))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.LeftButton:
            self._drag = None

    # ------------------------------------------------------------------ keys
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        if not self.handle_key(event):
            super().keyPressEvent(event)

    def handle_key(self, event: QKeyEvent) -> bool:
        """Act on a key meant for the timeline (the window passes on keys from elsewhere too)."""
        key, step = event.key(), BIG_STEP if event.modifiers() & Qt.ShiftModifier else 1
        if key in (Qt.Key_Left, Qt.Key_Right):
            self._nudge(step if key == Qt.Key_Right else -step)
        elif key in (Qt.Key_Home, Qt.Key_End):
            self.active = "playhead"
            self._edit_playhead(self.range.first if key == Qt.Key_Home else self.range.last)
        elif key == Qt.Key_I:
            self._edit_range(self.range.with_first(self.playhead), self.playhead)
        elif key == Qt.Key_O:
            self._edit_range(self.range.with_stop(self.playhead + 1), self.playhead)
        elif key == Qt.Key_BracketLeft:
            stop = self.selection[1] if self.selection and self.selection[1] > self.playhead else self.playhead + 1
            self._edit_selection((self.playhead, stop))
        elif key == Qt.Key_BracketRight:
            first = self.selection[0] if self.selection and self.selection[0] <= self.playhead else self.playhead
            self._edit_selection((first, self.playhead + 1))
        elif key == Qt.Key_Escape and self.selection is not None:
            self._edit_selection(None)
        elif key == Qt.Key_Space:
            self.play_toggled.emit()
        elif key == Qt.Key_Escape and self.active != "playhead":
            self.active = "playhead"
        else:
            return False
        self.update()
        return True

    def _nudge(self, frames: int) -> None:
        if self.active == "in":
            moved = self.range.nudge_in(frames)
            self._edit_range(moved, moved.first)
        elif self.active == "out":
            moved = self.range.nudge_out(frames)
            self._edit_range(moved, moved.last)
        else:
            self._edit_playhead(self.playhead + frames)

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = self.track_rect()
        shape = QPainterPath()
        shape.addRoundedRect(track, 6, 6)
        p.fillPath(shape, QColor(t.palette.surface2))
        p.save()
        p.setClipPath(shape)
        self._paint_thumbnails(p, track)
        x_in, x_out = self.x_for(self.range.start), self.x_for(self.range.end)
        p.fillRect(QRectF(track.left(), track.top(), x_in - track.left(), track.height()), DIM)
        p.fillRect(QRectF(x_out, track.top(), track.right() - x_out, track.height()), DIM)
        for first, stop in self.cuts:
            span = self._span_rect(first, stop, track)
            p.fillRect(span, CUT)
            p.fillRect(span, QBrush(QColor(255, 255, 255, 90), Qt.BDiagPattern))
        accent = QColor(t.accent.fill)
        if self.selection is not None:
            chosen = self._span_rect(*self.selection, track)
            fill = QColor(accent)
            fill.setAlpha(90)
            p.fillRect(chosen, fill)
            p.setPen(QPen(accent, 2))
            p.drawLine(chosen.topLeft(), chosen.bottomLeft())
            p.drawLine(chosen.topRight(), chosen.bottomRight())
        p.restore()
        p.setPen(QPen(accent, 3))
        p.drawLine(QPointF(x_in, track.top()), QPointF(x_out, track.top()))
        p.drawLine(QPointF(x_in, track.bottom()), QPointF(x_out, track.bottom()))
        for name in ("in", "out"):
            self._paint_handle(p, name, accent, QColor(t.accent.on))
        self._paint_playhead(p, QColor(t.palette.text), accent)
        p.end()

    def _span_rect(self, first: int, stop: int, track: QRectF) -> QRectF:
        left, right = self.x_for(self.frames.boundary(first)), self.x_for(self.frames.boundary(stop))
        return QRectF(left, track.top(), max(1.0, right - left), track.height())

    def _paint_thumbnails(self, p: QPainter, track: QRectF) -> None:
        if not self._thumb_times:
            return
        sample = self.thumbnails[self._thumb_times[0]]
        tile_w = max(8.0, track.height() * sample.width() / max(1, sample.height()))
        x = track.left()
        while x < track.right():
            seconds = self.seconds_at(x + tile_w / 2)
            n = bisect.bisect_left(self._thumb_times, seconds)
            nearest = min((self._thumb_times[i] for i in (n - 1, n) if 0 <= i < len(self._thumb_times)),
                          key=lambda s: abs(s - seconds))
            p.drawImage(QRectF(x, track.top(), tile_w, track.height()), self.thumbnails[nearest])
            x += tile_w

    def _paint_handle(self, p: QPainter, name: str, fill: QColor, grip: QColor) -> None:
        rect = self.handle_rect(name)
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(rect, 4, 4)
        p.setPen(QPen(grip, 1.5))
        middle = rect.center()
        for dx in (-2, 2):
            p.drawLine(QPointF(middle.x() + dx, middle.y() - 8), QPointF(middle.x() + dx, middle.y() + 8))
        if self.active == name and self.hasFocus():
            p.setPen(QPen(fill, 1.5, Qt.DotLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(-3, -2, 3, 2), 6, 6)

    def _paint_playhead(self, p: QPainter, colour: QColor, accent: QColor) -> None:
        middle = self.frames.boundary(self.playhead) + (self.frames.boundary(self.playhead + 1)
                                                         - self.frames.boundary(self.playhead)) / 2
        x = self.x_for(middle)
        p.setPen(QPen(colour, 2))
        p.drawLine(QPointF(x, 2), QPointF(x, self.height() - 2))
        p.setPen(Qt.NoPen)
        p.setBrush(accent if self.active == "playhead" and self.hasFocus() else colour)
        p.drawPolygon(QPolygonF([QPointF(x - 6, 0), QPointF(x + 6, 0), QPointF(x, 8)]))
