"""A dashed outline just outside the area being recorded, so you can see what's in the recording.

It never takes the mouse or the keyboard, and it's drawn outside the area and kept out of captures
(SetWindowDisplayAffinity), so it's never in the recording itself.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .. import theme
from ..countdown import exclude_from_capture

LINE = 2  # pixels, drawn this far outside the area


class AreaFrame(QWidget):
    def __init__(self, area: QRect) -> None:
        """`area` in Qt's coordinates (geometry.to_logical)."""
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAccessibleName("Area being recorded")
        self.area = QRect(area)
        self.setGeometry(area.adjusted(-LINE, -LINE, LINE, LINE))

    def showEvent(self, e) -> None:  # noqa: N802 - Qt API
        super().showEvent(e)
        exclude_from_capture(self)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        p = QPainter(self)
        pen = QPen(QColor(theme.current().palette.danger), LINE, Qt.DashLine)
        pen.setDashPattern([4, 3])
        p.setPen(pen)
        p.drawRect(self.rect().adjusted(LINE // 2, LINE // 2, -(LINE + 1) // 2, -(LINE + 1) // 2))
        p.end()
