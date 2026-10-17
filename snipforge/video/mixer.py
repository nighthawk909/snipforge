"""Microphone and system audio, mixed onto one timeline by their timestamps.

Each packet goes where its timestamp says on the recording's Clock. That turns the gaps Windows
leaves (it sends no system-audio packets at all while nothing plays) into silence, and drops sound
that falls in a pause. Packets within RESYNC_SECONDS of where the previous one ended are butted up
against it, so ordinary timestamp jitter never clicks.

A sound card's clock runs a little fast or slow against the system's (0.01-0.1%). When a source
drifts more than DRIFT_TOLERANCE from its timestamps, one sample is added or dropped in the middle
of its next packet: a correction too small to hear, instead of a jump every few minutes.

The mixed stream must be unbroken from zero: Qt's QAudioBufferInput ignores buffer timestamps and
plays buffers end to end. Pure Python is fast enough (48 kHz stereo is ~100k samples a second).
"""
from __future__ import annotations

import array
import threading
from dataclasses import dataclass, field

from .clock import Clock

RESYNC_SECONDS = 0.05  # further off than this: a real gap, jump to the timestamp
DRIFT_TOLERANCE = 0.002  # further off than this: correct by one sample per packet


@dataclass
class Source:
    name: str
    gain: float = 1.0
    next_index: int | None = None  # where the next packet goes if it follows on
    placed_end: int = 0  # end of the furthest sound placed, so nothing is placed twice
    chunks: list[tuple[int, array.array]] = field(default_factory=list)
    peak: float = 0.0  # loudest sample since levels() was last read
    max_peak: float = 0.0
    samples: int = 0  # frames placed
    resyncs: int = 0
    adjustments: int = 0  # frames added (+) or dropped (-) to follow a drifting clock


class Mixer:
    def __init__(self, clock: Clock, rate: int = 48000, channels: int = 2) -> None:
        self.clock, self.rate, self.channels = clock, rate, channels
        self.sources: dict[str, Source] = {}
        self.emitted = 0  # frames handed out by pull()
        self._lock = threading.Lock()

    def add_source(self, name: str, gain: float = 1.0) -> None:
        with self._lock:
            self.sources[name] = Source(name, gain)

    def push(self, name: str, payload: bytes, frames: int, t: float, discontinuity: bool) -> None:
        """A packet of interleaved float32 frames whose first frame was captured at wall time `t`."""
        samples = array.array("f")
        samples.frombytes(payload)
        with self._lock:
            src = self.sources[name]
            if samples:
                src.peak = max(src.peak, max(samples), -min(samples))
                src.max_peak = max(src.max_peak, src.peak)
            media = self.clock.media_time(t)
            if media is None:
                src.next_index = None  # paused: drop it, and start afresh afterwards
                return
            index, samples, frames = self._place(src, round(media * self.rate), samples, frames, discontinuity)
            src.next_index = index + frames
            if index < 0:  # straddles the start: keep only what comes after it
                samples, frames, index = samples[-index * self.channels:], frames + index, 0
            overlap = src.placed_end - index
            if overlap > 0:  # after a jump backwards: don't play the overlapping part twice
                samples, frames, index = samples[overlap * self.channels:], frames - overlap, index + overlap
            if frames <= 0 or index + frames <= self.emitted:
                return
            src.chunks.append((index, samples))
            src.placed_end = index + frames
            src.samples += frames

    def _place(self, src: Source, stamped: int, samples: array.array, frames: int,
               discontinuity: bool) -> tuple[int, array.array, int]:
        """Where the packet goes, and the packet, a sample longer or shorter to follow drift."""
        if src.next_index is None or discontinuity or abs(stamped - src.next_index) > RESYNC_SECONDS * self.rate:
            if src.next_index is not None:
                src.resyncs += 1
            return stamped, samples, frames
        error = stamped - src.next_index  # > 0: the card is slow and sound would come early
        if frames > 2 and abs(error) > DRIFT_TOLERANCE * self.rate:
            middle = frames // 2 * self.channels
            frame = samples[middle:middle + self.channels]
            if error > 0:
                samples = samples[:middle] + frame + samples[middle:]
                frames += 1
                src.adjustments += 1
            else:
                samples = samples[:middle] + samples[middle + self.channels:]
                frames -= 1
                src.adjustments -= 1
        return src.next_index, samples, frames

    def levels(self) -> dict[str, float]:
        """Each source's loudest sample since the last call (0-1), for level meters."""
        with self._lock:
            peaks = {name: src.peak for name, src in self.sources.items()}
            for src in self.sources.values():
                src.peak = 0.0
            return peaks

    def pull(self, until: float) -> tuple[int, array.array] | None:
        """The mix from where the last call ended up to `until` seconds, as (first frame, samples);
        None if there's nothing new. Sound that arrives for time already handed out is dropped."""
        with self._lock:
            start, end = self.emitted, round(until * self.rate)
            if end <= start:
                return None
            ch = self.channels
            out = array.array("f", bytes(4 * (end - start) * ch))
            for src in self.sources.values():
                keep = []
                for index, samples in src.chunks:
                    stop = index + len(samples) // ch
                    a, b = max(index, start), min(stop, end)
                    if a < b:
                        _add(out, (a - start) * ch, samples, (a - index) * ch, (b - a) * ch, src.gain)
                    if stop > end:
                        keep.append((index, samples))
                src.chunks = keep
            for i, v in enumerate(out):
                if v > 1.0:
                    out[i] = 1.0
                elif v < -1.0:
                    out[i] = -1.0
            self.emitted = end
            return start, out


def _add(out: array.array, at: int, samples: array.array, frm: int, count: int, gain: float) -> None:
    if gain == 1.0:
        for i in range(count):
            out[at + i] += samples[frm + i]
    else:
        for i in range(count):
            out[at + i] += samples[frm + i] * gain
