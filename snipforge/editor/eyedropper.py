"""The eyedropper's loupe: the pixels around the pointer, enlarged, with the one you'd pick outlined
and its color written underneath (drawn by snipforge/loupe.py, which Pick a color shares)."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont, QImage, QPainter
from PySide6.QtWidgets import QWidget

from .. import loupe

RADIUS = 5  # pixels on each side of the one you'd pick: an 11 x 11 sample
CELL = 10  # on-screen size of one sampled pixel
GAP = 18  # between the pointer and the loupe


def sample(source: QImage, x: int, y: int) -> QImage:
    """The (2 * RADIUS + 1)-pixel square around (x, y); outside the image is transparent."""
    return loupe.sample(source, x, y, RADIUS)


class Loupe(QWidget):
    """Floats over the canvas's viewport while picking. Mouse clicks go through it to the canvas."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFixedSize(loupe.size(RADIUS, CELL))
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
        font = QFont(self.font())
        font.setPointSizeF(9)
        font.setBold(True)
        loupe.paint(p, QPoint(0, 0), self._sample, self._label, radius=RADIUS, cell=CELL,
                    ratio=self.devicePixelRatioF() or 1.0, font=font)
        p.end()
