"""The eyedropper's loupe: the pixels around the pointer, enlarged, with the one you'd pick outlined
and its color written underneath."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

RADIUS = 5  # pixels on each side of the one you'd pick: an 11 x 11 sample
CELL = 10  # on-screen size of one sampled pixel
GAP = 18  # between the pointer and the loupe
LABEL_H = 24
SIDE = (2 * RADIUS + 1) * CELL


def sample(source: QImage, x: int, y: int) -> QImage:
    """The (2 * RADIUS + 1)-pixel square around (x, y); outside the image is transparent."""
    return source.copy(QRect(x - RADIUS, y - RADIUS, 2 * RADIUS + 1, 2 * RADIUS + 1))


class Loupe(QWidget):
    """Floats over the canvas's viewport while picking. Mouse clicks go through it to the canvas."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFixedSize(SIDE + 2, SIDE + 2 + 6 + LABEL_H)
        self._sample: QImage | None = None
        self._label = ""
        self.hide()

    def show_at(self, view_pos: QPoint, pixels: QImage, label: str) -> None:
        """Beside the pointer, on whichever side keeps it inside the canvas."""
        self._sample, self._label = pixels, label
        bounds = self.parentWidget().rect()
        x, y = view_pos.x() + GAP, view_pos.y() + GAP
        if x + self.width() > bounds.right():
            x = view_pos.x() - GAP - self.width()
        if y + self.height() > bounds.bottom():
            y = view_pos.y() - GAP - self.height()
        self.move(max(0, x), max(0, y))
        self.show()
        self.raise_()
        self.update()

    def label(self) -> str:
        return self._label

    def paintEvent(self, _e) -> None:  # noqa: N802 - Qt API
        p = QPainter(self)
        grid = QRect(1, 1, SIDE, SIDE)
        p.fillRect(grid, QColor("#3a3a3c"))  # behind anything outside the snip
        cell = CELL
        if self._sample is not None:
            # Whole device pixels per cell (12 at 125%, not 12.5), so every cell is the same size.
            ratio = self.devicePixelRatioF() or 1.0
            device_cell = max(1, round(CELL * ratio))
            n = 2 * RADIUS + 1
            enlarged = self._sample.scaled(n * device_cell, n * device_cell, Qt.IgnoreAspectRatio,
                                           Qt.FastTransformation)  # no smoothing: crisp squares
            enlarged.setDevicePixelRatio(ratio)
            p.drawImage(QPoint(1, 1), enlarged)
            cell = device_cell / ratio
        center = QRectF(1 + RADIUS * cell, 1 + RADIUS * cell, cell, cell)
        p.setPen(QPen(QColor("#000000"), 3))
        p.drawRect(center.adjusted(-1, -1, 1, 1))
        p.setPen(QPen(QColor("#ffffff"), 1))
        p.drawRect(center.adjusted(-1, -1, 1, 1))
        p.setPen(QPen(QColor(0, 0, 0, 160), 1))
        p.drawRect(QRectF(0.5, 0.5, SIDE + 1, SIDE + 1))
        if self._label:
            box = QRectF(0, SIDE + 2 + 6, SIDE + 2, LABEL_H)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(20, 20, 20, 235))
            p.drawRoundedRect(box, 6, 6)
            font = QFont(self.font())
            font.setPointSizeF(9)
            font.setBold(True)
            p.setFont(font)
            p.setPen(QColor("#ffffff"))
            p.drawText(box, Qt.AlignCenter, self._label)
        p.end()
