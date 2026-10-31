"""Text recognition using the OCR engine built into Windows (offline, no model download).

Windows only: elsewhere `recognize()` raises OcrUnavailable, so Grab Text explains why and Library
search leaves snips unread.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF
from PySide6.QtGui import QImage


@dataclass(frozen=True)
class OcrWord:
    text: str
    rect: QRectF


@dataclass(frozen=True)
class OcrLine:
    text: str
    words: tuple[OcrWord, ...]


@dataclass(frozen=True)
class OcrResult:
    lines: tuple[OcrLine, ...]

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


class OcrUnavailable(RuntimeError):
    """Windows can't do OCR here (e.g. no OCR language pack installed)."""


class OcrNotInstalled(OcrUnavailable):
    """SnipForge's OCR components are missing (a broken install or build)."""


def _png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    image.save(buf, "PNG")
    buf.close()
    return bytes(data)


async def _recognize_async(png: bytes):
    try:
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream
    except ImportError as exc:  # pragma: no cover - only when the bundle is broken
        raise OcrNotInstalled("Text recognition components are missing from this build.") from exc

    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise OcrUnavailable("No OCR language pack is installed in Windows.")
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(png)
    await writer.store_async()
    await writer.flush_async()
    writer.detach_stream()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    return await engine.recognize_async(bitmap)


OCR_MAX_SIDE = 9000  # under OcrEngine.MaxImageDimension (10000)


def _upscale_small(image: QImage) -> tuple[QImage, float]:
    """Windows OCR misses small UI text; upscaling small snips helps a lot."""
    short = min(image.width(), image.height())
    # Windows OCR refuses images over 10000 px on a side, so long, thin snips aren't doubled.
    scale = 2.0 if short < 600 and max(image.width(), image.height()) * 2 <= OCR_MAX_SIDE else 1.0
    if scale == 1.0:
        return image, 1.0
    return image.scaled(int(image.width() * scale), int(image.height() * scale)), scale


def recognize(image: QImage) -> OcrResult:
    """Blocking OCR; returns lines and word boxes in the image's pixel coordinates."""
    if sys.platform != "win32":
        raise OcrUnavailable("Text recognition isn't available on this computer yet: SnipForge uses the "
                             "OCR engine built into Windows.")
    import asyncio  # here, not at the top: it adds a tenth of a second to every start

    scaled, scale = _upscale_small(image)
    raw = asyncio.run(_recognize_async(_png_bytes(scaled)))
    lines = []
    for line in raw.lines:
        words = tuple(
            OcrWord(w.text, QRectF(w.bounding_rect.x / scale, w.bounding_rect.y / scale,
                                   w.bounding_rect.width / scale, w.bounding_rect.height / scale))
            for w in line.words
        )
        lines.append(OcrLine(line.text, words))
    return OcrResult(tuple(lines))
