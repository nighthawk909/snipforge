"""Regenerate assets/snipforge.ico (all Windows sizes) from the app's built-in icon drawing."""
from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtWidgets import QApplication

from snipforge.app import build_icon

SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841 - needed for QPixmap
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    build_icon().pixmap(256, 256).save(buffer, "PNG")
    image = Image.open(io.BytesIO(bytes(buffer.data())))
    out = Path(__file__).resolve().parents[1] / "assets" / "snipforge.ico"
    image.save(out, sizes=[(s, s) for s in SIZES])
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
