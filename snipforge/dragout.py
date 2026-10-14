"""A button you drag from to drop the finished snip into Outlook, Teams, PowerPoint, Explorer..."""
from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QDrag, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QPushButton

from .share import remove_stale

DRAG_DIR = Path(tempfile.gettempdir()) / "SnipForge"


class DragOutButton(QPushButton):
    def __init__(self, render_image, text: str = "Drag out", parent=None) -> None:
        super().__init__(text, parent)
        self._render_image = render_image  # callable -> QImage
        self._press: QPoint | None = None
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("Drag this into an email, chat, slide or folder")

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._press = e.position().toPoint()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self._press is None or not e.buttons() & Qt.LeftButton:
            return
        if (e.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        self._press = None
        self.setDown(False)
        self._start_drag(self._render_image())

    def mouseReleaseEvent(self, e) -> None:
        self._press = None
        super().mouseReleaseEvent(e)

    def _start_drag(self, image: QImage) -> None:
        DRAG_DIR.mkdir(parents=True, exist_ok=True)
        remove_stale(DRAG_DIR, "Snip *.png")
        path = DRAG_DIR / dt.datetime.now().strftime("Snip %Y-%m-%d %H%M%S.png")
        if not image.save(str(path)):
            return
        # File only, exactly what Explorer offers. Teams and other Chromium-based apps reject a drop
        # that also carries raw bitmap data ("file is missing something").
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        drag = QDrag(self)
        drag.setMimeData(mime)
        thumb = QPixmap.fromImage(image.scaled(160, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        drag.setPixmap(thumb)
        drag.setHotSpot(QPoint(thumb.width() // 2, thumb.height() // 2))
        drag.exec(Qt.CopyAction)
