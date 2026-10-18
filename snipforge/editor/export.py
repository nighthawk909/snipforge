"""Writing a snip to a file: PNG, JPG, WebP, PDF or BMP, chosen by the file's extension.

Files are written through QSaveFile: a save that fails part-way (a full disk, a format's limit)
leaves any earlier file at that path as it was.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QIODevice, QMarginsF, QRectF, QSaveFile, QSizeF, Qt
from PySide6.QtGui import QImage, QPageLayout, QPageSize, QPainter, QPdfWriter

JPG_QUALITY = 92  # text in screenshots blurs at Qt's default of 75
WEBP_QUALITY = 100  # Qt's WebP writer is lossless at 100: sharp text, and smaller than PNG
WEBP_MAX_SIDE = 16383  # the WebP format's own limit
PDF_DPI = 96  # one snip pixel is one CSS pixel, so the page is the snip's size on screen
PDF_MAX_POINTS = 14400  # 200 inches: the biggest page PDF readers promise to open; bigger snips get a smaller page
EXTENSIONS = {".png": "PNG", ".jpg": "JPG", ".jpeg": "JPG", ".webp": "WEBP", ".bmp": "BMP", ".pdf": "PDF"}
SAVE_FILTERS = "PNG (*.png);;JPEG (*.jpg *.jpeg);;WebP (*.webp);;PDF (*.pdf);;Bitmap (*.bmp)"


class ExportError(Exception):
    """A snip that can't be saved in the format asked for; the message says why and what to do."""


def with_suffix(path: str, chosen_filter: str) -> Path:
    """`path`, with the extension of the type picked in the Save dialog if it has none."""
    result = Path(path)
    if result.suffix.lower() in EXTENSIONS:
        return result
    for ext in EXTENSIONS:
        if f"*{ext}" in chosen_filter:
            return result.with_name(result.name + ext)
    return result.with_name(result.name + ".png")


def filter_for(path: Path) -> str:
    """The Save dialog's entry for `path`'s type, to start the dialog on."""
    ext = path.suffix.lower()
    return next((f for f in SAVE_FILTERS.split(";;") if f"*{ext}" in f), SAVE_FILTERS.split(";;")[0])


def fits(image: QImage, path: Path) -> bool:
    """Whether `image` can be saved in `path`'s format at all (WebP has a size limit)."""
    kind = EXTENSIONS.get(path.suffix.lower(), "PNG")
    return kind != "WEBP" or max(image.width(), image.height()) <= WEBP_MAX_SIDE


def on_white(image: QImage) -> QImage:
    """`image` over white, for formats without transparency (and paper)."""
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
    Returns False if it couldn't be written; raises ExportError if the format can't hold it."""
    kind = EXTENSIONS.get(path.suffix.lower(), "PNG")
    if not fits(image, path):
        raise ExportError(f"WebP can't be bigger than {WEBP_MAX_SIDE:,} px on a side, and this snip is "
                          f"{image.width():,} × {image.height():,}. Save it as PNG or PDF instead.")
    if kind in ("JPG", "BMP", "PDF"):
        image = on_white(image)  # no transparency there (or on paper): torn edges would turn black
    out = QSaveFile(str(path))
    if not out.open(QIODevice.WriteOnly):
        return False
    if kind == "PDF":
        ok = _write_pdf(image, out, path.stem)
    else:
        ok = image.save(out, kind, {"JPG": JPG_QUALITY, "WEBP": WEBP_QUALITY}.get(kind, -1))
    if not ok:
        out.cancelWriting()
        return False
    return out.commit()


def _write_pdf(image: QImage, device: QIODevice, title: str) -> bool:
    """One page the snip's size (smaller if the snip is huge), with the snip on it, no margins, and
    the pixels kept exactly (no JPEG)."""
    writer = QPdfWriter(device)
    writer.setResolution(PDF_DPI)
    writer.setTitle(title)
    writer.setCreator("SnipForge")
    points = QSizeF(image.width() * 72 / PDF_DPI, image.height() * 72 / PDF_DPI)
    shrink = min(1.0, PDF_MAX_POINTS / max(points.width(), points.height()))
    page = QPageSize(points * shrink, QPageSize.Point, "", QPageSize.ExactMatch)
    writer.setPageLayout(QPageLayout(page, QPageLayout.Portrait, QMarginsF(0, 0, 0, 0)))
    painter = QPainter()
    if not painter.begin(writer):
        return False
    try:
        painter.setRenderHint(QPainter.LosslessImageRendering)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        target = QRectF(writer.pageLayout().fullRectPixels(PDF_DPI))  # the whole page, whatever its rounding
        painter.drawImage(target, image)
    finally:
        painter.end()
    return True
