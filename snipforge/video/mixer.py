"""Microphone and system audio, mixed onto one timeline by their timestamps.

Each packet goes where its timestamp says on the recording's Clock. That turns the gaps Windows
leaves (it sends no system-audio packets at all while nothing plays) into silence, and drops sound
that falls in a pause. Small differences are timestamp jitter: the packet is butted up against the
previous one, so nothing clicks.

Timestamps aren't trusted blindly. One that Windows flags as unreliable (passed as None), one from
the future, or one that jumps backwards (sound can't arrive from the past) is ignored, and the packet
simply follows on from the last one: a single bad stamp must never silence a source for the rest of
the recording.

A sound card's clock runs a little fast or slow against the system's (0.01-0.1%). When a source
drifts more than DRIFT_TOLERANCE from its timestamps, one sample (the average of its neighbours) is
added, or one dropped, in the middle of its next packet: too small to hear, and 10 ms packets can
follow drift of up to 0.2%.

The mixed stream must be unbroken from zero: Qt's QAudioBufferInput ignores buffer timestamps and
plays buffers end to end. pull_ready() keeps HOLD_BACK seconds back so packets still on their way
(they arrive 10-25 ms after they're stamped, more when the PC is busy) aren't lost; any that come
later anyway are counted per source as late_frames. Pure Python is fast enough: about 2-5% of one
core for two sources.
"""
from __future__ import annotations

import array
import itertools
import operator
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .clock import Clock

JUMP_SECONDS = 0.015  # a forward gap larger than this is real silence: jump to the timestamp
DRIFT_TOLERANCE = 0.002  # off by more than this (but less than a jump): correct by one sample per packet
FUTURE_TOLERANCE = 0.05  # a stamp this far after "now" can't be right
HOLD_BACK = 0.25  # seconds pull_ready() leaves for packets still on their way


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
    resyncs: int = 0  # gaps jumped
    adjustments: int = 0  # frames added (+) or dropped (-) to follow a drifting clock
    bad_stamps: int = 0  # packets whose timestamp was ignored
    late_frames: int = 0  # frames that arrived after their time had been handed out


class Mixer:
    def __init__(self, clock: Clock, rate: int = 48000, channels: int = 2,
                 now: Callable[[], float] = time.perf_counter) -> None:
        self.clock, self.rate, self.channels, self.now = clock, rate, channels, now
        self.sources: dict[str, Source] = {}
        self.emitted = 0  # frames handed out by pull()
        self._lock = threading.Lock()

    def add_source(self, name: str, gain: float = 1.0) -> None:
        with self._lock:
            self.sources[name] = Source(name, gain)

    def push(self, name: str, payload: bytes, frames: int, t: float | None, discontinuity: bool) -> None:
        """A packet of interleaved float32 frames whose first frame was captured at wall time `t`
        (None when Windows says the timestamp is unreliable)."""
        samples = array.array("f")
        samples.frombytes(payload)
        with self._lock:
            src = self.sources[name]
            if samples:
                src.peak = max(src.peak, max(samples), -min(samples))
                src.max_peak = max(src.max_peak, src.peak)
            stamped = self._stamp(src, t)
            if stamped is None:
                return
            index, samples, frames = self._place(src, stamped, samples, frames, discontinuity and t is not None)
            src.next_index = index + frames
            self._keep(src, index, samples, frames)

    def _stamp(self, src: Source, t: float | None) -> int | None:
        """The frame a packet's timestamp points at, or None to drop the packet."""
        if t is not None and t > self.now() + FUTURE_TOLERANCE:
            t = None  # can't have been captured in the future
        if t is None:
            src.bad_stamps += 1
            if src.next_index is None or self.clock.paused:
                src.next_index = None
                return None  # nothing to follow on from
            return src.next_index
        media = self.clock.media_time(t)
        if media is None:
            src.next_index = None  # paused: drop it, and start afresh afterwards
            return None
        return round(media * self.rate)

    def _place(self, src: Source, stamped: int, samples: array.array, frames: int,
               discontinuity: bool) -> tuple[int, array.array, int]:
        """Where the packet goes, and the packet, a sample longer or shorter to follow drift."""
        if src.next_index is None:
            return stamped, samples, frames
        error = stamped - src.next_index  # > 0: later than following on would put it
        if error > JUMP_SECONDS * self.rate or (discontinuity and error > DRIFT_TOLERANCE * self.rate):
            src.resyncs += 1  # a real gap: silence until it
            return stamped, samples, frames
        if error < -JUMP_SECONDS * self.rate:
            src.bad_stamps += 1  # sound can't come from the past: the stamp is wrong, follow on
            return src.next_index, samples, frames
        if frames > 2 and abs(error) > DRIFT_TOLERANCE * self.rate:
            ch = self.channels
            middle = frames // 2 * ch
            if error > 0:
                between = array.array("f", ((a + b) / 2 for a, b in zip(samples[middle - ch:middle],
                                                                        samples[middle:middle + ch], strict=True)))
                samples = samples[:middle] + between + samples[middle:]
                frames += 1
                src.adjustments += 1
            else:
                samples = samples[:middle] + samples[middle + ch:]
                frames -= 1
                src.adjustments -= 1
        return src.next_index, samples, frames

    def _keep(self, src: Source, index: int, samples: array.array, frames: int) -> None:
        """Store what's left of a packet once the parts that can't be played are cut off."""
        ch = self.channels
        cut = max(0, -index, src.placed_end - index)  # before the start, or already placed
        late = self.emitted - (index + cut)
        if late > 0:  # its time has already been handed out
            src.late_frames += min(late, frames - cut)
            cut += late
        if cut >= frames:
            return
        if cut:
            samples, frames, index = samples[cut * ch:], frames - cut, index + cut
        src.chunks.append((index, samples))
        src.placed_end = index + frames
        src.samples += frames

    def levels(self) -> dict[str, float]:
        """Each source's loudest sample since the last call (0-1), for level meters."""
        with self._lock:
            peaks = {name: src.peak for name, src in self.sources.items()}
            for src in self.sources.values():
                src.peak = 0.0
            return peaks

    def pull_ready(self, now: float) -> tuple[int, array.array] | None:
        """The mix up to HOLD_BACK seconds before wall time `now` (None while paused or if nothing
        is ready): what a recorder hands the encoder as it goes."""
        media = self.clock.media_time(now)
        return None if media is None else self.pull(media - HOLD_BACK)

    def pull(self, until: float) -> tuple[int, array.array] | None:
        """The mix from where the last call ended up to `until` seconds, as (first frame, samples);
        None if there's nothing new. Sound that arrives later for that time is dropped and counted."""
        with self._lock:
            start, end = self.emitted, round(until * self.rate)
            if end <= start:
                return None
            ch = self.channels
            out = array.array("f", bytes(4 * (end - start) * ch))
            for first_source, src in enumerate(self.sources.values()):
                keep = []
                for index, samples in src.chunks:
                    stop = index + len(samples) // ch
                    a, b = max(index, start), min(stop, end)
                    if a < b:
                        piece = samples[(a - index) * ch:(b - index) * ch]
                        _add(out, (a - start) * ch, piece, src.gain, empty=first_source == 0)
                    if stop > end:
                        keep.append((index, samples))
                src.chunks = keep
            if out and (max(out) > 1.0 or min(out) < -1.0):
                out = array.array("f", (min(1.0, max(-1.0, v)) for v in out))
            self.emitted = end
            return start, out


def _add(out: array.array, at: int, piece: array.array, gain: float, empty: bool) -> None:
    """Add `piece` into `out` at `at`. A source's own chunks never overlap, so the first source can
    simply be copied in; C-level map() keeps the rest quick (no per-sample Python loop)."""
    if gain != 1.0:
        piece = array.array("f", map(operator.mul, piece, itertools.repeat(gain)))
    end = at + len(piece)
    out[at:end] = piece if empty else array.array("f", map(operator.add, out[at:end], piece))
