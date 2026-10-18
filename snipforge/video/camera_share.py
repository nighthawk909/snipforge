"""The webcam's picture, from SnipForge to its recorder process, and where its bubble is.

SnipForge owns the camera and the bubble showing it: a window repainting live video in the recorder
process would hold Python's lock and starve the frame thread (the control bar alone once did). The
recorder only needs the newest picture, so SnipForge writes each one into a small block of shared
memory, named at random for this recording, and the recorder's frame thread reads it there.

A sequence number guards the block (a "seqlock"): the writer makes it odd while it writes and even
when it's done; the reader copies the picture and keeps it only if the number was even and unchanged
throughout. So a picture is never read half written, and neither side ever waits for the other.
"""
from __future__ import annotations

import re
import secrets
import struct
import threading
from multiprocessing import shared_memory

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage

from .webcam_bubble import BubbleSnapshot
from .webcam_layer import SHAPES

NAME_PREFIX = "SnipForge-camera-"
NAME = re.compile(r"SnipForge-camera-[0-9a-f]{32}")
MAX_WIDTH, MAX_HEIGHT = 1280, 720  # bigger pictures are shrunk to fit: the bubble is small anyway
HEADER = struct.Struct("<8sQIII")  # magic, sequence, width, height, bytes per line
MAGIC = b"SFCAM001"
DATA = 64  # where the picture starts, after the header
SIZE = DATA + MAX_WIDTH * MAX_HEIGHT * 4


def is_name(text: str) -> bool:
    return bool(NAME.fullmatch(text or ""))


class CameraWriter:
    """SnipForge's end: write(image) for each picture from the camera."""

    def __init__(self) -> None:
        self.name = NAME_PREFIX + secrets.token_hex(16)
        self._memory = shared_memory.SharedMemory(name=self.name, create=True, size=SIZE)
        HEADER.pack_into(self._memory.buf, 0, MAGIC, 0, 0, 0, 0)
        self._sequence = 0

    def write(self, image: QImage) -> None:
        if image.isNull():
            return
        if image.width() > MAX_WIDTH or image.height() > MAX_HEIGHT:
            image = image.scaled(QSize(MAX_WIDTH, MAX_HEIGHT), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        image = image.convertToFormat(QImage.Format.Format_RGB32)
        size = image.bytesPerLine() * image.height()
        self._begin_write()
        self._memory.buf[DATA:DATA + size] = bytes(image.constBits())[:size]
        self._sequence += 1  # even: whole again
        HEADER.pack_into(self._memory.buf, 0, MAGIC, self._sequence, image.width(), image.height(),
                         image.bytesPerLine())

    def _begin_write(self) -> None:
        self._sequence += 1  # odd: being written
        struct.pack_into("<Q", self._memory.buf, 8, self._sequence)

    def close(self) -> None:
        self._memory.close()
        try:
            self._memory.unlink()  # gone once the reader lets go too (Windows: when the last handle closes)
        except FileNotFoundError:
            pass


class CameraReader:
    """The recorder's end: latest() for the frame thread (webcam_layer.CameraPictures)."""

    def __init__(self, name: str) -> None:
        if not is_name(name):
            raise ValueError(f"{name!r} isn't a camera share SnipForge makes")
        self._memory = shared_memory.SharedMemory(name=name, create=False)
        self._sequence = -1
        self._image: QImage | None = None

    def latest(self) -> QImage | None:
        """The newest whole picture, or the last one read while a new one is being written."""
        magic, sequence, width, height, stride = HEADER.unpack_from(self._memory.buf, 0)
        if magic != MAGIC or sequence == self._sequence or sequence % 2 or not width or not height:
            return self._image
        if not (0 < width <= MAX_WIDTH and 0 < height <= MAX_HEIGHT and width * 4 <= stride <= MAX_WIDTH * 4):
            return self._image  # not something SnipForge wrote
        data = bytes(self._memory.buf[DATA:DATA + stride * height])
        if struct.unpack_from("<Q", self._memory.buf, 8)[0] != sequence:
            return self._image  # rewritten while it was copied: keep the last whole one
        self._image = QImage(data, width, height, stride, QImage.Format.Format_RGB32).copy()
        self._sequence = sequence
        return self._image

    def close(self) -> None:
        self._memory.close()


class BubbleState:
    """Where the bubble is, kept up to date by SnipForge's messages, for the frame thread
    (webcam_layer.BubblePlace). A place that makes no sense is ignored; None means it's hidden."""

    def __init__(self, rect, shape: str) -> None:
        self._lock = threading.Lock()
        self._snapshot: BubbleSnapshot | None = None
        self.update(rect, shape)

    def update(self, rect, shape: str) -> None:
        if rect is None:
            with self._lock:
                self._snapshot = None
            return
        if shape not in SHAPES or not isinstance(rect, (list, tuple)) or len(rect) != 4 \
                or not all(isinstance(v, int) and not isinstance(v, bool) for v in rect) \
                or rect[2] <= 0 or rect[3] <= 0:
            return
        with self._lock:
            self._snapshot = BubbleSnapshot(tuple(rect), shape)

    def snapshot(self) -> BubbleSnapshot | None:
        with self._lock:
            return self._snapshot
