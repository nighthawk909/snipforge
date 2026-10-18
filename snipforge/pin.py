"""Pin a snip to the screen as a floating, always-on-top reference window."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QWidget

from . import theme
from .wording import keys

MIN_SCALE, MAX_SCALE = 0.1, 4.0


class PinWindow(QWidget):
    def __init__(self, image: QImage, at: QPoint | None = None) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._pixmap = QPixmap.fromImage(image)
        self._scale = 1.0
        self._drag_offset: QPoint | None = None
        avail = QGuiApplication.primaryScreen().availableGeometry()
        if image.width() > avail.width() * 0.6:
            self._scale = avail.width() * 0.6 / image.width()
        self._resize()
        self.move(at or avail.center() - self.rect().center())
        self.setToolTip(f"Drag to move • {keys('wheel')} to zoom • {keys('Ctrl+wheel')} opacity • "
                        "double-click to close")

    def _resize(self) -> None:
        self.resize(max(24, int(self._pixmap.width() * self._scale)) + 2,
                    max(24, int(self._pixmap.height() * self._scale)) + 2)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(self.rect().adjusted(1, 1, -1, -1), self._pixmap)
        p.setPen(QPen(QColor(theme.current().accent.fill), 1))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e) -> None:
        if self._drag_offset is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, _e) -> None:
        self._drag_offset = None

    def mouseDoubleClickEvent(self, _e) -> None:
        self.close()

    def wheelEvent(self, e) -> None:
        up = e.angleDelta().y() > 0
        if e.modifiers() & Qt.ControlModifier:
            self.setWindowOpacity(min(1.0, max(0.2, self.windowOpacity() + (0.1 if up else -0.1))))
            return
        self._scale = min(MAX_SCALE, max(MIN_SCALE, self._scale * (1.1 if up else 1 / 1.1)))
        self._resize()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self.close()

    def contextMenuEvent(self, e) -> None:
        menu = QMenu(self)
        menu.addAction("Copy", lambda: QGuiApplication.clipboard().setPixmap(self._pixmap))
        menu.addAction("Actual size", self._actual_size)
        menu.addSeparator()
        menu.addAction("Close", self.close)
        menu.exec(e.globalPos())

    def _actual_size(self) -> None:
        self._scale = 1.0
        self._resize()
