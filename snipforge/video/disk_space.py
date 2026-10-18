"""Free space on the drive a recording is saved to.

A recording that fills the drive can't be finished, so it would be lost: the recorder won't start
without room, warns when space runs low, and stops (keeping what was recorded) before the drive is
full. While recording, space is read on a thread of its own, since a network drive that stalls can
take seconds to answer and the recording mustn't wait for it. A drive that can't be read is
"unknown", not "full": Qt itself reports a drive that's really gone.
"""
from __future__ import annotations

import ctypes
import logging
import shutil
import sys
import threading
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

MB = 1024 * 1024
MIN_FREE_TO_START = 500 * MB  # a 1080p recording takes very roughly 50-100 MB a minute
LOW_SPACE = 1024 * MB  # warn once below this
MIN_FREE_TO_GO_ON = 200 * MB  # stop below this: room left to finish the file
CHECK_SECONDS = 5.0


def free_bytes(folder: Path) -> int:
    """Bytes this Windows user may still write in `folder` (disk quotas count); OSError if the
    folder can't be read."""
    if sys.platform != "win32":
        return shutil.disk_usage(folder).free
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    available = ctypes.c_ulonglong()
    if not kernel32.GetDiskFreeSpaceExW(ctypes.c_wchar_p(str(folder)), ctypes.byref(available), None, None):
        raise ctypes.WinError(ctypes.get_last_error())
    return available.value


class SpaceWatch:
    """Reads free space in `folder` every `seconds` on its own thread. `latest` is the newest
    reading: None before the first, and while the drive can't be read."""

    def __init__(self, folder: Path, read: Callable[[Path], int] = free_bytes,
                 seconds: float = CHECK_SECONDS) -> None:
        self.folder, self.read, self.seconds = folder, read, seconds
        self.latest: int | None = None
        self.reads = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._unreadable_logged = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="disk-space", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(1.0)  # a stalled drive may keep it longer; it's a daemon thread

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.latest = self.read(self.folder)
            except OSError as exc:
                self.latest = None
                if not self._unreadable_logged:
                    self._unreadable_logged = True
                    log.warning("Can't read the free space in %s: %s", self.folder, exc)
            self.reads += 1
            self._stop.wait(self.seconds)
