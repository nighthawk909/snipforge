"""The webcam's bubble: a small floating window showing the camera, mirrored like a mirror.

The person drags it where they want it and right-clicks to change its shape (circle, rounded
rectangle, rectangle) or size. It never takes keyboard focus, so typing goes on in the app being
recorded, and it's kept out of the capture (WDA_EXCLUDEFROMCAPTURE): the recording draws the camera
into each frame itself (webcam_layer.py), at the place the bubble shows, so the camera is never
captured twice or half-covered by another window.

The frame thread asks where the bubble is about 30 times a second. Reading a widget's geometry from
another thread isn't safe, so the bubble keeps a snapshot (its rectangle in physical desktop pixels,
and its shape), replaced under a lock whenever it moves, resizes, changes shape, shows or hides.
"""
from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QGuiApplication, QImage, QPainter
from PySide6.QtWidgets import QMenu, QWidget

from ..countdown import exclude_from_capture
from .webcam_layer import CIRCLE, RECTANGLE, ROUNDED, SHAPES, paint_camera, shape_path

SMALL, MEDIUM, LARGE = "small", "medium", "large"
HEIGHTS = {SMALL: 128, MEDIUM: 192, LARGE: 288}  # logical pixels; a circle's diameter
RECTANGLE_ASPECT = 4 / 3  # most webcams' own shape, so little of the picture is cropped away
MARGIN = 24  # from the screen's edge, when the bubble has to be put somewhere new
SHAPE_NAMES = {CIRCLE: "Circle", ROUNDED: "Rounded rectangle", RECTANGLE: "Rectangle"}
SIZE_NAMES = {SMALL: "Small", MEDIUM: "Medium", LARGE: "Large"}
PLACEHOLDER = QColor(40, 40, 44, 235)
PLACEHOLDER_TEXT = QColor(220, 220, 220)


@dataclass(frozen=True)
class BubbleSnapshot:
    """Where the bubble is (x, y, width, height in physical desktop pixels) and its shape. Frozen,
    so the thread that reads one can't change it under the others."""
    rect: tuple[int, int, int, int]
    shape: str


def bubble_size(shape: str, size: str) -> QSize:
    """The bubble's size in logical pixels: square for a circle, 4:3 otherwise."""
    height = HEIGHTS[size]
    return QSize(height if shape == CIRCLE else round(height * RECTANGLE_ASPECT), height)


def to_physical(rect: QRect, screen_geometry: QRect, ratio: float) -> QRect:
    """`rect` (logical pixels, on the monitor whose logical geometry is `screen_geometry`) in physical
    desktop pixels. Qt keeps each monitor's top-left at its physical position and scales only within
    the monitor, by its device pixel ratio."""
    gx, gy = screen_geometry.x(), screen_geometry.y()
    return QRect(round(gx + (rect.x() - gx) * ratio), round(gy + (rect.y() - gy) * ratio),
                 round(rect.width() * ratio), round(rect.height() * ratio))


def to_logical(point: QPoint, screens: Sequence[tuple[QRect, float]]) -> QPoint | None:
    """A physical desktop point in Qt's logical pixels, given each monitor's (logical geometry, device
    pixel ratio); None if it's on no monitor."""
    for geometry, ratio in screens:
        gx, gy = geometry.x(), geometry.y()
        physical = QRect(gx, gy, round(geometry.width() * ratio), round(geometry.height() * ratio))
        if physical.contains(point):
            return QPoint(round(gx + (point.x() - gx) / ratio), round(gy + (point.y() - gy) / ratio))
    return None


class WebcamBubble(QWidget):
    """The camera on screen. Give it a Webcam (or connect its `frame` to set_image yourself)."""

    moved = Signal(QRect)  # the person dragged it somewhere new: its rectangle in physical pixels
    shape_changed = Signal(str)
    size_changed = Signal(str)

    def __init__(self, camera=None, *, shape: str = CIRCLE, size: str = MEDIUM, parent: QWidget | None = None) -> None:
        if shape not in SHAPES:
            raise ValueError(f"A bubble can be {', '.join(SHAPES)}, not {shape!r}")
        if size not in HEIGHTS:
            raise ValueError(f"A bubble can be {', '.join(HEIGHTS)}, not {size!r}")
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)  # a circle's corners show what's behind
        self.setFocusPolicy(Qt.NoFocus)
        self.setAccessibleName("Webcam")
        self.setToolTip("Drag to move. Right-click to change its shape or size.")
        self.setCursor(Qt.OpenHandCursor)
        self._shape, self._size = shape, size
        self._image: QImage | None = None
        self._drag_offset: QPoint | None = None
        self._lock = threading.Lock()
        self._snapshot: BubbleSnapshot | None = None
        self._watching_screen = False
        self.resize(bubble_size(shape, size))
        if camera is not None:
            camera.frame.connect(self.set_image)

    # --- what it shows ---------------------------------------------------------------------------

    def shape(self) -> str:
        return self._shape

    def bubble_size(self) -> str:
        return self._size

    def set_shape(self, shape: str) -> None:
        if shape not in SHAPES:
            raise ValueError(f"A bubble can be {', '.join(SHAPES)}, not {shape!r}")
        if shape != self._shape:
            self._shape = shape
            self._resize_keeping_centre()
            self.shape_changed.emit(shape)

    def set_bubble_size(self, size: str) -> None:
        if size not in HEIGHTS:
            raise ValueError(f"A bubble can be {', '.join(HEIGHTS)}, not {size!r}")
        if size != self._size:
            self._size = size
            self._resize_keeping_centre()
            self.size_changed.emit(size)

    def set_image(self, image: QImage | None) -> None:
        """The camera's newest picture (None: waiting for the camera)."""
        self._image = image
        self.update()

    def _resize_keeping_centre(self) -> None:
        geometry = QRect(QPoint(0, 0), bubble_size(self._shape, self._size))
        geometry.moveCenter(self.geometry().center())
        self.setGeometry(geometry)
        self.update()
        self._refresh_snapshot()  # the shape may change without the geometry changing

    # --- where it is -----------------------------------------------------------------------------

    def area_rect(self) -> QRect:
        """The bubble's rectangle in physical desktop pixels, as recordings measure the screen."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        return to_physical(self.geometry(), screen.geometry(), screen.devicePixelRatio())

    def snapshot(self) -> BubbleSnapshot | None:
        """Where the bubble is and its shape, or None while it's hidden. Safe from any thread."""
        with self._lock:
            return self._snapshot

    def move_physical(self, x: int, y: int) -> None:
        """Puts the bubble's top-left at physical desktop pixel (x, y), as area_rect() reported it.
        If no monitor is there any more, it goes to the main monitor's bottom-right corner."""
        screens = [(screen.geometry(), screen.devicePixelRatio()) for screen in QGuiApplication.screens()]
        point = to_logical(QPoint(x, y), screens)
        if point is None:
            available = QGuiApplication.primaryScreen().availableGeometry()
            point = QPoint(available.right() + 1 - self.width() - MARGIN,
                           available.bottom() + 1 - self.height() - MARGIN)
        self.move(point)

    def _refresh_snapshot(self) -> None:
        snap = BubbleSnapshot(self.area_rect().getRect(), self._shape) if self.isVisible() else None
        with self._lock:
            self._snapshot = snap

    # --- Qt events -------------------------------------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        exclude_from_capture(self)  # every time: Windows forgets it if the native window is remade
        if not self._watching_screen and self.windowHandle() is not None:
            # Dragged onto a monitor with other scaling: the same logical size is a new physical one.
            self.windowHandle().screenChanged.connect(lambda _screen: self._refresh_snapshot())
            self._watching_screen = True
        self._refresh_snapshot()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._refresh_snapshot()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._refresh_snapshot()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_snapshot()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        try:
            target = QRectF(self.rect())
            if self._image is not None and not self._image.isNull():
                paint_camera(painter, self._image, target, self._shape)
            else:
                self._paint_waiting(painter, target)
        finally:
            painter.end()

    def _paint_waiting(self, painter: QPainter, target: QRectF) -> None:
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(PLACEHOLDER)
        painter.drawPath(shape_path(target, self._shape))
        painter.setPen(PLACEHOLDER_TEXT)
        painter.drawText(target, Qt.AlignCenter, "Starting the camera…")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self.setCursor(Qt.OpenHandCursor)
            self.moved.emit(self.area_rect())
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        # popup(), not exec(): no event loop inside an event loop while a recording runs.
        self.context_menu().popup(event.globalPos())

    def context_menu(self) -> QMenu:
        """Shape and size, the current ones ticked."""
        menu = QMenu(self)
        menu.setAttribute(Qt.WA_DeleteOnClose, True)
        menu.addSection("Shape")
        self._add_choices(menu, SHAPE_NAMES, self._shape, self.set_shape)
        menu.addSection("Size")
        self._add_choices(menu, SIZE_NAMES, self._size, self.set_bubble_size)
        return menu

    @staticmethod
    def _add_choices(menu: QMenu, names: dict[str, str], current: str, choose) -> None:
        group = QActionGroup(menu)
        for value, name in names.items():
            action = QAction(name, menu, checkable=True, checked=value == current)
            action.triggered.connect(lambda _checked=False, v=value: choose(v))
            group.addAction(action)
            menu.addAction(action)
