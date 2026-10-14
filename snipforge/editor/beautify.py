"""Optional 'presentation' styling: padding, gradient backdrop, rounded corners, shadow."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath


@dataclass(frozen=True)
class BeautifyStyle:
    padding: int = 56
    radius: int = 14
    shadow: int = 28
    start: str = "#7f5af0"
    end: str = "#2cb1bc"


def apply_beautify(image: QImage, style: BeautifyStyle = BeautifyStyle()) -> QImage:
    pad = style.padding
    out = QImage(image.width() + pad * 2, image.height() + pad * 2, QImage.Format_ARGB32_Premultiplied)
    p = QPainter(out)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)

    gradient = QLinearGradient(0, 0, out.width(), out.height())
    gradient.setColorAt(0, QColor(style.start))
    gradient.setColorAt(1, QColor(style.end))
    p.fillRect(out.rect(), gradient)

    card = QRectF(pad, pad, image.width(), image.height())
    p.setPen(Qt.NoPen)
    for i in range(style.shadow, 0, -2):  # stacked translucent layers fake a blur
        p.setBrush(QColor(0, 0, 0, 5))
        p.drawRoundedRect(card.adjusted(-i, -i + 10, i, i + 10), style.radius + i, style.radius + i)

    clip = QPainterPath()
    clip.addRoundedRect(card, style.radius, style.radius)
    p.setClipPath(clip)
    p.drawImage(card.topLeft(), image)
    p.end()
    return out
