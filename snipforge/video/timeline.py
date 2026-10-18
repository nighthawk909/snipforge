"""The video editor's timeline: when each frame starts, and which frames a trim keeps.

Frame times come from the file's packets (read_frame_times), not from a frame rate, because
recordings drop frames when the PC is busy and so don't have an even one. A trim always starts and
ends on a frame boundary, which is exactly what trim.copy and frames.read cut on, so what the editor
shows is what the copy and the GIF contain.

FrameTimes and TrimRange are values: every change returns a new one.
"""
from __future__ import annotations

import bisect
import os
import statistics
from ctypes import byref
from dataclasses import dataclass, replace

from . import ffmpeg

EPSILON = 1e-6
AV_PKT_FLAG_DISCARD = 0x0004  # frames an edit list hides (before a trimmed copy's start)


@dataclass(frozen=True)
class FrameTimes:
    """When each frame starts (seconds, ascending) and when the last one ends."""

    starts: tuple[float, ...]
    end: float

    def __post_init__(self) -> None:
        if not self.starts:
            raise ValueError("A video needs at least one frame")
        if any(b <= a for a, b in zip(self.starts, self.starts[1:], strict=False)):
            raise ValueError("Frame times must go forwards")
        if self.end <= self.starts[-1]:
            raise ValueError("The last frame must end after it starts")

    @classmethod
    def uniform(cls, fps: float, count: int) -> FrameTimes:
        return cls(tuple(n / fps for n in range(count)), count / fps)

    def __len__(self) -> int:
        return len(self.starts)

    def boundary(self, index: int) -> float:
        """Where frame `index` starts; index len(self) is where the last frame ends."""
        return self.end if index >= len(self.starts) else self.starts[max(0, index)]

    def index_at(self, seconds: float) -> int:
        """The frame showing at `seconds` (the first or last frame outside the video)."""
        return min(len(self.starts) - 1, max(0, bisect.bisect_right(self.starts, seconds + EPSILON) - 1))

    def nearest_boundary(self, seconds: float) -> int:
        """The frame boundary (0 to len(self)) nearest to `seconds`."""
        after = bisect.bisect_left(self.starts, seconds)
        candidates = [i for i in (after - 1, after) if 0 <= i <= len(self.starts)]
        return min(candidates, key=lambda i: abs(self.boundary(i) - seconds))


@dataclass(frozen=True)
class TrimRange:
    """The frames a trim keeps: from frame `first` up to, not including, frame `stop`."""

    frames: FrameTimes
    first: int
    stop: int

    @classmethod
    def whole(cls, frames: FrameTimes) -> TrimRange:
        return cls(frames, 0, len(frames))

    @property
    def start(self) -> float:
        return self.frames.boundary(self.first)

    @property
    def end(self) -> float:
        return self.frames.boundary(self.stop)

    @property
    def seconds(self) -> float:
        return self.end - self.start

    @property
    def last(self) -> int:
        """The last frame kept."""
        return self.stop - 1

    @property
    def is_whole(self) -> bool:
        return self.first == 0 and self.stop == len(self.frames)

    @property
    def export_end(self) -> float | None:
        """The end to hand trim.copy and the GIF export: None when the range runs to the end of the
        video, so the sound after the last frame's start comes along too."""
        return None if self.stop == len(self.frames) else self.end

    def keeps(self, index: int) -> bool:
        return self.first <= index < self.stop

    def with_first(self, index: int) -> TrimRange:
        return replace(self, first=max(0, min(index, self.stop - 1)))

    def with_stop(self, index: int) -> TrimRange:
        return replace(self, stop=max(self.first + 1, min(index, len(self.frames))))

    def move_in_to(self, seconds: float) -> TrimRange:
        return self.with_first(self.frames.nearest_boundary(seconds))

    def move_out_to(self, seconds: float) -> TrimRange:
        return self.with_stop(self.frames.nearest_boundary(seconds))

    def nudge_in(self, frames: int) -> TrimRange:
        return self.with_first(self.first + frames)

    def nudge_out(self, frames: int) -> TrimRange:
        return self.with_stop(self.stop + frames)


def format_time(seconds: float) -> str:
    """0:03.25, or 1:02:05.50 past an hour."""
    hundredths = max(0, round(seconds * 100))
    minutes, rest = divmod(hundredths, 6000)
    hours, minutes = divmod(minutes, 60)
    seconds_text = f"{rest // 100:02d}.{rest % 100:02d}"
    return f"{hours}:{minutes:02d}:{seconds_text}" if hours else f"{minutes}:{seconds_text}"


def read_frame_times(path: str | os.PathLike) -> FrameTimes:
    """When each frame of `path`'s video starts, from its packets (nothing is decoded, so this is
    quick even for long recordings), counted from its first frame as frames.read does."""
    ctx = ffmpeg.open_input(path)
    try:
        stream = ffmpeg.video_stream(ctx)
        if stream is None:
            raise ffmpeg.FFmpegError(f"{os.path.basename(os.fspath(path))} has no video")
        origin = max(0.0, ffmpeg.seconds(stream.start_time, stream.time_base) or 0.0)
        stamps = _packet_times(ctx, stream, os.fspath(path))
    finally:
        ffmpeg.close_input(ctx)
    starts = sorted({round(start - origin, 6) for start, _ in stamps if start - origin > -EPSILON})
    if not starts:
        raise ffmpeg.FFmpegError(f"{os.path.basename(os.fspath(path))} has no frames")
    ends = [start + length - origin for start, length in stamps if length > 0]
    gaps = [b - a for a, b in zip(starts, starts[1:], strict=False)]
    end = max([*ends, starts[-1] + (statistics.median(gaps) if gaps else 1 / 30)])
    return FrameTimes(tuple(max(0.0, s) for s in starts), end)


def _packet_times(ctx, stream: ffmpeg.AVStream, name: str) -> list[tuple[float, float]]:
    """(start, length) in seconds for each video packet the file doesn't mark to be skipped."""
    libs = ffmpeg.libs()
    fmt, codec_lib = libs["avformat"], libs["avcodec"]
    base = stream.time_base
    packet = codec_lib.av_packet_alloc()
    found = []
    try:
        while (code := fmt.av_read_frame(ctx, packet)) >= 0:
            p = packet.contents
            stamp = p.pts if p.pts != ffmpeg.AV_NOPTS_VALUE else p.dts
            if p.stream_index == stream.index and stamp != ffmpeg.AV_NOPTS_VALUE and not p.flags & AV_PKT_FLAG_DISCARD:
                found.append((stamp * base.num / base.den, max(0, p.duration) * base.num / base.den))
            codec_lib.av_packet_unref(packet)
        if code != ffmpeg.AVERROR_EOF:
            raise ffmpeg.FFmpegError(f"Couldn't read {name}: {ffmpeg.message(code)}")
    finally:
        codec_lib.av_packet_free(byref(packet))
    return found
