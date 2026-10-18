"""When each frame of a video starts, for the editor's timeline, without decoding anything.

An MP4's index (its moov box) lists every frame's time, so for MP4, which is what SnipForge records,
this reads only the index: quick even for an hour-long recording, and it doesn't make OneDrive
download the whole file. Times count from the first frame shown, as frames.read and trim.copy do,
and the frames an edit list hides before a trimmed copy's start are left out.

The index holds decoding times, which are the frames' times only when the video has no B-frames
(Qt's encoder makes none). Other videos, and other containers, fall back to reading every packet,
which reads the whole file.
"""
from __future__ import annotations

import os
import statistics
from ctypes import POINTER, Structure, byref, c_int, c_int64, c_uint

from . import ffmpeg
from .timeline import FrameTimes

EPSILON = 1e-6
AVINDEX_DISCARD_FRAME = 0x0002  # in an index entry's flags: hidden by an edit list
AV_PKT_FLAG_DISCARD = 0x0004  # the same, on a packet
FALLBACK_FPS = 30  # for the last frame's length, when a one-frame video doesn't say


class AVIndexEntry(Structure):  # FFmpeg 9.0.1's libavformat/avformat.h
    _fields_ = [("pos", c_int64), ("timestamp", c_int64), ("flags", c_uint, 2), ("size", c_uint, 30),
                ("min_distance", c_int)]


def _declare_index(fmt) -> None:
    fmt.avformat_index_get_entries_count.restype = c_int
    fmt.avformat_index_get_entries_count.argtypes = [POINTER(ffmpeg.AVStream)]
    fmt.avformat_index_get_entry.restype = POINTER(AVIndexEntry)
    fmt.avformat_index_get_entry.argtypes = [POINTER(ffmpeg.AVStream), c_int]


def read(path: str | os.PathLike) -> FrameTimes:
    """When each frame of `path`'s video starts, and when the last one ends. Raises FFmpegError if
    the file can't be read or has no video."""
    name = os.path.basename(os.fspath(path))
    ctx = ffmpeg.open_input(path)
    try:
        stream_p = _video_stream(ctx)
        if stream_p is None:
            raise ffmpeg.FFmpegError(f"{name} has no video")
        stream = stream_p.contents
        stamps = _index_times(ctx, stream_p) or _packet_times(ctx, stream, name)
        base = stream.time_base
        origin = max(0.0, ffmpeg.seconds(stream.start_time, base) or 0.0)
        length = ffmpeg.seconds(stream.duration, base)
        stream_end = None if length is None else (ffmpeg.seconds(stream.start_time, base) or 0.0) + length
    finally:
        ffmpeg.close_input(ctx)
    return _frame_times(stamps, origin, stream_end, name)


def _video_stream(ctx):
    """A pointer to the file's video stream (not a cover picture), as the index functions want."""
    fc = ctx.contents
    for i in range(fc.nb_streams):
        stream = fc.streams[i].contents
        cover = stream.disposition & ffmpeg.AV_DISPOSITION_ATTACHED_PIC
        if ffmpeg.kind(stream) == ffmpeg.AVMEDIA_TYPE_VIDEO and not cover:
            return fc.streams[i]
    return None


def _index_times(ctx, stream_p) -> list[tuple[float, float]]:
    """(start, 0) per frame from an MP4's index; empty when the index can't be trusted for this."""
    stream = stream_p.contents
    container = (ctx.contents.iformat.contents.name or b"").decode()
    if "mp4" not in container.split(",") or stream.codecpar.contents.video_delay > 0:
        return []  # not an MP4, or B-frames: decoding order isn't showing order
    fmt = ffmpeg.libs()["avformat"]
    _declare_index(fmt)
    count = fmt.avformat_index_get_entries_count(stream_p)
    if count <= 0 or count < stream.nb_frames:
        return []  # an index still being built (fragmented MP4): read the packets instead
    base = stream.time_base
    found = []
    for i in range(count):
        entry = fmt.avformat_index_get_entry(stream_p, i).contents
        if not entry.flags & AVINDEX_DISCARD_FRAME:
            found.append((entry.timestamp * base.num / base.den, 0.0))
    return found


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


def _frame_times(stamps: list[tuple[float, float]], origin: float, stream_end: float | None,
                 name: str) -> FrameTimes:
    """Times counted from the first frame shown; the last frame ends where the stream says, else
    where its packet says, else one usual frame gap later."""
    starts = sorted({round(start - origin, 6) for start, _ in stamps if start - origin > -EPSILON})
    if not starts:
        raise ffmpeg.FFmpegError(f"{name} has no frames")
    starts = [max(0.0, s) for s in starts]
    gaps = [b - a for a, b in zip(starts, starts[1:], strict=False)]
    ends = [start + length - origin for start, length in stamps if length > 0]
    if stream_end is not None:
        ends.append(stream_end - origin)
    end = max(ends, default=0.0)
    if end <= starts[-1] + EPSILON:
        end = starts[-1] + (statistics.median(gaps) if gaps else 1 / FALLBACK_FPS)
    return FrameTimes(tuple(starts), end)
