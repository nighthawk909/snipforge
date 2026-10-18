"""The editor's picture: the current frame, scaled to fit and centred on the editor's well colour."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import theme


class VideoView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.image: QImage | None = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAccessibleName("Video")
        self.setMinimumSize(320, 180)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(800, 450)

    def set_image(self, image: QImage) -> None:
        self.image = image
        self.update()

    def picture_rect(self) -> QRectF:
        """Where the frame is drawn: as large as fits, keeping its shape."""
        if self.image is None or self.image.isNull():
            return QRectF()
        scale = min(self.width() / self.image.width(), self.height() / self.image.height())
        w, h = self.image.width() * scale, self.image.height() * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        t = theme.current()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(t.palette.well))
        target = self.picture_rect()
        if target.isEmpty():
            p.setPen(QColor(t.palette.text3))
            p.drawText(self.rect(), Qt.AlignCenter, "Loading the video…")
        else:
            p.setRenderHint(QPainter.SmoothPixmapTransform, target.width() < self.image.width())
            p.drawImage(target, self.image)
        p.end()
