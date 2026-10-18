"""A sound level meter: a small bar that jumps up to each new peak and falls back gently, so speech
reads as a steady flicker rather than a strobe."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from .. import theme

FALL = 0.82  # share of the level kept per update when no louder peak arrives
LOUD = 0.9  # peaks above this show in the warning colour: the sound may be distorting


class LevelMeter(QWidget):
    def __init__(self, name: str, parent=None) -> None:
        super().__init__(parent)
        self.level = 0.0
        self.available = True
        self.setAccessibleName(f"{name} level")
        self.setFixedSize(QSize(44, 8))

    def set_level(self, peak: float | None) -> None:
        """The loudest moment since the last update, 0-1; None when the source isn't recording."""
        self.available = peak is not None
        self.level = max(min(peak or 0.0, 1.0), self.level * FALL) if self.available else 0.0
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(t.palette.border))
        p.drawRoundedRect(QRectF(self.rect()), 4, 4)
        if self.available and self.level > 0.01:
            fill = QRectF(0, 0, max(8.0, self.width() * self.level), self.height())
            p.setBrush(QColor(t.palette.danger if self.level > LOUD else t.accent.fill))
            p.drawRoundedRect(fill, 4, 4)
        p.end()
