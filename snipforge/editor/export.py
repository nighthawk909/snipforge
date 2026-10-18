"""Writing a snip to a file: PNG, JPG, WebP, PDF or BMP, chosen by the file's extension."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMarginsF, QSizeF, Qt
from PySide6.QtGui import QImage, QPageLayout, QPageSize, QPainter, QPdfWriter

JPG_QUALITY = 92  # text in screenshots blurs at Qt's default of 75
WEBP_QUALITY = 100  # Qt's WebP writer is lossless at 100: sharp text, and smaller than PNG
PDF_DPI = 96  # one snip pixel is one CSS pixel, so the page is the snip's size on screen
EXTENSIONS = {".png": "PNG", ".jpg": "JPG", ".jpeg": "JPG", ".webp": "WEBP", ".bmp": "BMP", ".pdf": "PDF"}
SAVE_FILTERS = "PNG (*.png);;JPEG (*.jpg *.jpeg);;WebP (*.webp);;PDF (*.pdf);;Bitmap (*.bmp)"


def with_suffix(path: str, chosen_filter: str) -> Path:
    """`path`, with the extension of the type picked in the Save dialog if it has none."""
    result = Path(path)
    if result.suffix.lower() in EXTENSIONS:
        return result
    for ext in EXTENSIONS:
        if f"*{ext}" in chosen_filter:
            return result.with_name(result.name + ext)
    return result.with_name(result.name + ".png")


def on_white(image: QImage) -> QImage:
    """`image` over white, for formats without transparency."""
    if not image.hasAlphaChannel():
        return image
    out = QImage(image.size(), QImage.Format_RGB32)
    out.fill(Qt.white)
    painter = QPainter(out)
    painter.drawImage(0, 0, image)
    painter.end()
    return out


def save_image(image: QImage, path: Path) -> bool:
    """Write `image` to `path` in the format its extension names (PNG for anything else).
    Returns False if it couldn't be written."""
    kind = EXTENSIONS.get(path.suffix.lower(), "PNG")
    if kind == "PDF":
        return _save_pdf(image, path)
    if kind in ("JPG", "BMP"):
        image = on_white(image)  # no transparency in these formats: torn edges would turn black
    quality = {"JPG": JPG_QUALITY, "WEBP": WEBP_QUALITY}.get(kind, -1)
    return image.save(str(path), kind, quality)


def _save_pdf(image: QImage, path: Path) -> bool:
    """One page exactly the snip's size, with the snip on it and no margins."""
    writer = QPdfWriter(str(path))
    writer.setResolution(PDF_DPI)
    writer.setTitle(path.stem)
    writer.setCreator("SnipForge")
    points = QSizeF(image.width() * 72 / PDF_DPI, image.height() * 72 / PDF_DPI)
    layout = QPageLayout(QPageSize(points, QPageSize.Point, "", QPageSize.ExactMatch), QPageLayout.Portrait,
                         QMarginsF(0, 0, 0, 0))
    writer.setPageLayout(layout)
    painter = QPainter()
    if not painter.begin(writer):
        return False
    try:
        painter.drawImage(0, 0, image)
    finally:
        painter.end()
    return path.exists() and path.stat().st_size > 0
