"""A recording's timeline: wall-clock seconds (time.perf_counter, Windows' QueryPerformanceCounter)
turned into seconds of recording, with the time spent paused taken out.

Sound and picture are both placed by this clock, which is what keeps them together through pauses,
stalls and dropped frames. Safe to use from several threads.
"""
from __future__ import annotations

import threading


class Clock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.start: float | None = None
        self._pauses: list[tuple[float, float]] = []  # finished pauses, (from, to) in wall seconds
        self._paused_at: float | None = None

    def begin(self, now: float) -> None:
        with self._lock:
            self.start = now
            self._pauses.clear()
            self._paused_at = None

    @property
    def paused(self) -> bool:
        return self._paused_at is not None

    @property
    def paused_total(self) -> float:
        """Seconds spent in finished pauses."""
        with self._lock:
            return sum(b - a for a, b in self._pauses)

    def pause(self, now: float) -> None:
        with self._lock:
            if self.start is not None and self._paused_at is None:
                self._paused_at = now

    def resume(self, now: float) -> None:
        with self._lock:
            if self._paused_at is not None:
                self._pauses.append((self._paused_at, now))
                self._paused_at = None

    def media_time(self, wall: float) -> float | None:
        """Seconds into the recording at wall-clock time `wall`: negative before it starts, None
        before it has been begun or while it was paused."""
        with self._lock:
            if self.start is None:
                return None
            offset = 0.0
            for a, b in self._pauses:
                if wall >= b:
                    offset += b - a
                elif wall >= a:
                    return None
            if self._paused_at is not None and wall >= self._paused_at:
                return None
            return wall - self.start - offset
