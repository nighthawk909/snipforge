"""The webcam's picture, from SnipForge to its recorder process, and where its bubble is.

SnipForge owns the camera and the bubble showing it: a window repainting live video in the recorder
process would hold Python's lock and starve the frame thread (the control bar alone once did). The
recorder only needs the newest picture, so SnipForge writes each one into a small block of shared
memory, named at random for this recording, and the recorder's frame thread reads it there.

A sequence number guards the block (a "seqlock"): the writer makes it odd while it writes and even
when it's done; the reader copies the picture and keeps it only if the number was even and unchanged
throughout. Neither side ever waits for the other. (Python sets no memory barriers: on x86 that's
exact; on ARM a picture could, rarely, mix two neighbouring frames. Everything read is bounds-checked,
so the worst is a picture that's briefly a little off.)

A picture that stops changing for STALE_SECONDS (the camera was unplugged, or SnipForge closed it) is
dropped rather than frozen into the rest of the recording.
"""
from __future__ import annotations

import re
import secrets
import struct
import sys
import threading
import time
from multiprocessing import shared_memory

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage

from .webcam_bubble import BubbleSnapshot
from .webcam_layer import SHAPES

NAME_PREFIX = "sfcam-"  # short: macOS allows 31 characters for a shared memory name
NAME = re.compile(r"sfcam-[0-9a-f]{16}")
MAX_WIDTH, MAX_HEIGHT = 1280, 720  # bigger pictures are shrunk to fit: the bubble is small anyway
HEADER = struct.Struct("<8sQIII")  # magic, sequence, width, height, bytes per line
SEQUENCE = 8  # where the sequence number sits in the header
MAGIC = b"SFCAM001"
DATA = 64  # where the picture starts, after the header
SIZE = DATA + MAX_WIDTH * MAX_HEIGHT * 4
STALE_SECONDS = 2.0  # a live camera never goes this long without a new picture
MAX_PLACE, MAX_SIDE = 100_000, 16_384  # a bubble's place and size, in physical pixels, can't exceed these


def is_name(text) -> bool:
    return isinstance(text, str) and bool(NAME.fullmatch(text))


class CameraWriter:
    """SnipForge's end: write(image) for each picture from the camera."""

    def __init__(self) -> None:
        self.name = NAME_PREFIX + secrets.token_hex(8)
        self._memory = shared_memory.SharedMemory(name=self.name, create=True, size=SIZE)
        HEADER.pack_into(self._memory.buf, 0, MAGIC, 0, 0, 0, 0)
        self._sequence = 0  # always even between writes, even if one fails

    def write(self, image: QImage) -> None:
        if image.isNull():
            return
        if image.width() > MAX_WIDTH or image.height() > MAX_HEIGHT:
            image = image.scaled(QSize(MAX_WIDTH, MAX_HEIGHT), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        image = image.convertToFormat(QImage.Format.Format_RGB32)
        size = image.bytesPerLine() * image.height()
        writing = self._sequence + 1  # odd: being written
        struct.pack_into("<Q", self._memory.buf, SEQUENCE, writing)
        self._memory.buf[DATA:DATA + size] = bytes(image.constBits())[:size]
        HEADER.pack_into(self._memory.buf, 0, MAGIC, writing, image.width(), image.height(), image.bytesPerLine())
        self._sequence = writing + 1  # even: whole again (only now: a write that fails leaves it even)
        struct.pack_into("<Q", self._memory.buf, SEQUENCE, self._sequence)

    def close(self) -> None:
        self._memory.close()
        try:
            self._memory.unlink()  # gone once the reader lets go too (Windows: when the last handle closes)
        except FileNotFoundError:
            pass


class CameraReader:
    """The recorder's end: latest() for the frame thread (webcam_layer.CameraPictures)."""

    def __init__(self, name: str, now=time.monotonic) -> None:
        if not is_name(name):
            raise ValueError(f"{name!r} isn't a camera share SnipForge makes")
        self._memory = _attach(name)
        if self._memory.size < SIZE:
            self._memory.close()
            raise ValueError(f"{name} is too small to be a camera share SnipForge makes")
        self._now = now
        self._sequence = -1
        self._image: QImage | None = None
        self._fresh = now()  # when a new picture last came

    def latest(self) -> QImage | None:
        """The newest whole picture (the last one, while a new one is being written); None before the
        first, and once none has come for STALE_SECONDS."""
        image = self._read()
        if image is not None:
            self._image, self._fresh = image, self._now()
        elif self._now() - self._fresh > STALE_SECONDS:
            self._image = None
        return self._image

    def _read(self) -> QImage | None:
        magic, sequence, width, height, stride = HEADER.unpack_from(self._memory.buf, 0)
        if magic != MAGIC or sequence == self._sequence or sequence % 2:
            return None
        if not (0 < width <= MAX_WIDTH and 0 < height <= MAX_HEIGHT and width * 4 <= stride <= MAX_WIDTH * 4):
            return None  # not something SnipForge wrote
        data = self._copy(stride * height)
        if len(data) != stride * height or struct.unpack_from("<Q", self._memory.buf, SEQUENCE)[0] != sequence:
            return None  # rewritten while it was copied: keep the last whole one
        self._sequence = sequence
        return QImage(data, width, height, stride, QImage.Format.Format_RGB32).copy()

    def _copy(self, size: int) -> bytes:
        return bytes(self._memory.buf[DATA:DATA + size])

    def close(self) -> None:
        self._memory.close()


def _attach(name: str) -> shared_memory.SharedMemory:
    """Open SnipForge's block without taking charge of it: on macOS and Linux, Python's resource
    tracker would otherwise delete it when the recorder exits, and warn about a "leak"."""
    if sys.version_info >= (3, 13):
        return shared_memory.SharedMemory(name=name, create=False, track=False)
    memory = shared_memory.SharedMemory(name=name, create=False)
    if sys.platform != "win32":
        from multiprocessing import resource_tracker

        resource_tracker.unregister(memory._name, "shared_memory")  # noqa: SLF001 - Python's own name for it
    return memory


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
                or not all(abs(v) <= MAX_PLACE for v in rect[:2]) or not all(0 < v <= MAX_SIDE for v in rect[2:]):
            return
        with self._lock:
            self._snapshot = BubbleSnapshot(tuple(rect), shape)

    def snapshot(self) -> BubbleSnapshot | None:
        with self._lock:
            return self._snapshot
