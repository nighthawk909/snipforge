"""Sound levels before a recording starts, for the ready panel's meters: so you can see the microphone
is live (and not muted or blocked) before you begin.

Each source chosen gets a WASAPI capture of its own, read only for its loudest moment; nothing is
kept. A source that can't be opened (Windows' privacy setting, no device) says why instead.
"""
from __future__ import annotations

import array
import logging
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)


class LevelPreview:
    """`capture(kind, device_id, on_packet, on_error)` makes a capture like the recorder's (start(),
    stop()); `kind` is "microphone" or "system"."""

    def __init__(self, capture: Callable | None = None) -> None:
        self._make_capture = capture or _real_capture
        self._captures: dict[str, object] = {}
        self._peaks: dict[str, float] = {}
        self.problems: dict[str, str] = {}  # why a source couldn't be opened
        self._lock = threading.Lock()

    def set_source(self, kind: str, on: bool, device_id: str | None = None) -> None:
        """Listen to `kind` (again, on `device_id`), or stop listening."""
        self._close(kind)
        self.problems.pop(kind, None)
        if not on:
            return
        capture = self._make_capture(kind, device_id, self._packet_handler(kind), lambda error: None)
        try:
            capture.start()
        except Exception as exc:  # AudioUnavailable, MicrophoneBlocked: say so on the meter
            log.info("No level preview for the %s: %s", kind, exc)
            self.problems[kind] = str(exc)
            return
        self._captures[kind] = capture

    def levels(self) -> dict[str, float | None]:
        """Each source's loudest moment since the last call (0-1); None for one that isn't open."""
        with self._lock:
            peaks, self._peaks = self._peaks, {}
        return {kind: (peaks.get(kind, 0.0) if kind in self._captures else None)
                for kind in ("microphone", "system")}

    def close(self) -> None:
        for kind in list(self._captures):
            self._close(kind)

    def _close(self, kind: str) -> None:
        capture = self._captures.pop(kind, None)
        if capture is not None:
            capture.stop()

    def _packet_handler(self, kind: str):
        def heard(payload: bytes, frames: int, _stamp, _glitch) -> None:  # on the capture thread
            samples = array.array("f")
            samples.frombytes(payload[: len(payload) // 4 * 4])
            peak = max(max(samples, default=0.0), -min(samples, default=0.0))
            with self._lock:
                self._peaks[kind] = max(self._peaks.get(kind, 0.0), peak)
        return heard


def _real_capture(kind, device_id, on_packet, on_error):
    from . import wasapi

    return wasapi.WasapiCapture(wasapi.MICROPHONE if kind == "microphone" else wasapi.SYSTEM, on_packet,
                                device_id=device_id, on_error=on_error)
