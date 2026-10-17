"""Mixes microphone and system audio onto one timeline, by their QPC timestamps.

Each source's packets are placed at the sample their timestamp says, minus time spent paused.
Gaps (loopback sends nothing while the PC is silent) become silence; a packet within 30 ms of
where the previous one ended is butted up against it, so normal timestamp jitter never clicks.

Production notes (see docs/VIDEO-PLAN.md): device clocks drift from QPC by up to ~0.01%, which this
corrects by jumping once a source is 30 ms off (an audible tick every few minutes in a long
recording). The real mixer should resample slightly instead. Pure Python is fast enough here
(48 kHz stereo is ~100k samples a second); numpy isn't needed.
"""
from __future__ import annotations

import array
import threading
from dataclasses import dataclass, field

RESYNC_SECONDS = 0.03


@dataclass
class Source:
    name: str
    gain: float = 1.0
    next_index: int | None = None
    chunks: list[tuple[int, array.array]] = field(default_factory=list)
    peak: float = 0.0  # since the last level read, for the meters
    max_peak: float = 0.0
    resyncs: int = 0
    samples: int = 0


class Mixer:
    def __init__(self, sample_rate: int, channels: int, start: float) -> None:
        self.rate, self.channels, self.start = sample_rate, channels, start
        self.sources: dict[str, Source] = {}
        self._lock = threading.Lock()
        self._paused_at: float | None = None
        self._paused_total = 0.0
        self._pauses: list[tuple[float, float]] = []  # (wall start, wall end) of finished pauses
        self.emitted = 0  # timeline samples (per channel) handed to the encoder

    def add_source(self, name: str, gain: float = 1.0) -> Source:
        self.sources[name] = Source(name, gain)
        return self.sources[name]

    # Pause bookkeeping, on the same perf_counter clock as the packets.
    def pause(self, now: float) -> None:
        with self._lock:
            self._paused_at = now

    def resume(self, now: float) -> None:
        with self._lock:
            if self._paused_at is not None:
                self._pauses.append((self._paused_at, now))
                self._paused_total += now - self._paused_at
                self._paused_at = None

    def media_time(self, wall: float) -> float | None:
        """Seconds into the recording for a wall-clock time, or None if it fell in a pause."""
        offset = 0.0
        for a, b in self._pauses:
            if wall >= b:
                offset += b - a
            elif wall >= a:
                return None
        if self._paused_at is not None and wall >= self._paused_at:
            return None
        return wall - self.start - offset

    def push(self, name: str, payload: bytes, frames: int, t: float, discontinuity: bool) -> None:
        samples = array.array("f")
        samples.frombytes(payload)
        with self._lock:
            src = self.sources[name]
            if samples:
                src.peak = max(src.peak, max(samples), -min(samples))
                src.max_peak = max(src.max_peak, src.peak)
            media = self.media_time(t)
            if media is None or media + frames / self.rate <= 0:
                src.next_index = None  # paused or before the start: drop, and resync afterwards
                return
            index = round(media * self.rate)
            if (src.next_index is None or discontinuity
                    or abs(index - src.next_index) > RESYNC_SECONDS * self.rate):
                if src.next_index is not None:
                    src.resyncs += 1
                src.next_index = index
            index = src.next_index
            if index < 0:  # straddles the start
                samples = samples[-index * self.channels:]
                frames += index
                index = 0
            src.chunks.append((index, samples))
            src.next_index = index + frames
            src.samples += frames

    def levels(self) -> dict[str, float]:
        with self._lock:
            out = {name: s.peak for name, s in self.sources.items()}
            for s in self.sources.values():
                s.peak = 0.0
            return out

    def pull(self, until_index: int) -> tuple[int, array.array] | None:
        """Mixed samples from `emitted` up to `until_index` (timeline samples), or None if none."""
        with self._lock:
            start = self.emitted
            count = until_index - start
            if count <= 0:
                return None
            ch = self.channels
            out = array.array("f", bytes(4 * count * ch))
            for src in self.sources.values():
                keep = []
                for index, samples in src.chunks:
                    end = index + len(samples) // ch
                    a, b = max(index, start), min(end, until_index)
                    if a < b:
                        seg_from, seg_to = (a - index) * ch, (b - index) * ch
                        o = (a - start) * ch
                        gain = src.gain
                        for i in range(seg_to - seg_from):
                            out[o + i] += samples[seg_from + i] * gain
                    if end > until_index:
                        keep.append((index, samples))
                src.chunks = keep
            for i in range(len(out)):
                v = out[i]
                if v > 1.0:
                    out[i] = 1.0
                elif v < -1.0:
                    out[i] = -1.0
            self.emitted = until_index
            return start, out
