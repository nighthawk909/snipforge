"""Trimming a video, or copying it into MP4, without re-encoding: no quality lost, done in moments.

The copy starts at the keyframe before `start`; those extra frames get times before zero, which the
MP4 writer turns into an edit list, so players start exactly at `start` to the frame. Sound is cut
to the nearest audio packet (about 20 ms). Uses the FFmpeg libraries Qt ships, local files only.
"""
from __future__ import annotations

import os
from ctypes import POINTER, byref, c_char_p, c_void_p
from pathlib import Path

from . import ffmpeg

AVERROR_EOF = -0x20464F45
AVSEEK_FLAG_BACKWARD = 1
AVIO_FLAG_WRITE = 2
AUDIO_PREROLL = 0.05  # seconds of sound kept before the cut, so the decoder starts cleanly
EPSILON = 1e-6


def copy(src: str | os.PathLike, dst: str | os.PathLike, start: float = 0.0, end: float | None = None) -> Path:
    """Copy `src` from `start` up to `end` (seconds, end not included; None for the rest) into the
    MP4 file `dst`. `dst` is written whole or not at all."""
    if "://" in os.fspath(dst) or "://" in os.fspath(src):
        raise ValueError("Only local files can be trimmed")
    src, dst = Path(src), Path(dst)
    if start < 0 or (end is not None and end <= start):
        raise ValueError(f"Nothing to keep between {start} and {end} s")
    if dst.exists() and src.exists() and os.path.samefile(src, dst):
        raise ValueError("A trim can't overwrite the video it comes from")
    partial = dst.with_name(f".{dst.stem}.partial{dst.suffix or '.mp4'}")
    try:
        _copy(src, partial, start, end)
        os.replace(partial, dst)
    finally:
        partial.unlink(missing_ok=True)
    return dst


def _copy(src: Path, dst: Path, start: float, end: float | None) -> None:
    libs = ffmpeg.libs()
    fmt, codec_lib = libs["avformat"], libs["avcodec"]
    ictx = ffmpeg.open_input(src)
    octx = POINTER(ffmpeg.AVFormatContext)()
    packet = codec_lib.av_packet_alloc()
    pb = c_void_p()
    try:
        streams = [ictx.contents.streams[i].contents for i in range(ictx.contents.nb_streams)]
        origin = _origin(streams)
        length = ffmpeg.seconds(ictx.contents.duration, ffmpeg.AVRational(1, ffmpeg.AV_TIME_BASE))
        if length is not None and start >= length - origin - EPSILON:
            raise ValueError(f"{src.name} is only {length - origin:.2f} s long")
        code = fmt.avformat_alloc_output_context2(byref(octx), None, b"mp4", os.fspath(dst).encode("utf-8"))
        if code < 0:
            raise ffmpeg.FFmpegError(f"Couldn't create {dst.name}: {ffmpeg.message(code)}")
        bases = []
        for stream in streams:
            out = fmt.avformat_new_stream(octx, None)
            if not out or codec_lib.avcodec_parameters_copy(out.contents.codecpar, stream.codecpar) < 0:
                raise ffmpeg.FFmpegError(f"Couldn't copy the streams of {src.name}")
            out.contents.codecpar.contents.codec_tag = 0  # let the MP4 writer pick its own tag
            out.contents.time_base = stream.time_base
            bases.append(stream.time_base)
        options = ffmpeg.local_options()
        try:
            code = fmt.avio_open2(byref(pb), c_char_p(os.fspath(dst).encode("utf-8")), AVIO_FLAG_WRITE, None,
                                  byref(options))
        finally:
            libs["avutil"].av_dict_free(byref(options))
        if code < 0:
            raise ffmpeg.FFmpegError(f"Couldn't write {dst.name}: {ffmpeg.message(code)}")
        octx.contents.pb = pb
        code = fmt.avformat_write_header(octx, None)
        if code < 0:
            raise ffmpeg.FFmpegError(f"Couldn't write {dst.name}: {ffmpeg.message(code)}")
        out_bases = [octx.contents.streams[i].contents.time_base for i in range(len(streams))]
        first, last = start + origin, None if end is None else end + origin
        if start > EPSILON:
            fmt.av_seek_frame(ictx, -1, round(first * ffmpeg.AV_TIME_BASE), AVSEEK_FLAG_BACKWARD)
        _copy_packets(ictx, octx, packet, streams, bases, out_bases, first, last)
        code = fmt.av_write_trailer(octx)
        if code < 0:
            raise ffmpeg.FFmpegError(f"Couldn't finish {dst.name}: {ffmpeg.message(code)}")
    finally:
        if pb:
            fmt.avio_closep(byref(pb))
        if octx:
            octx.contents.pb = None
            fmt.avformat_free_context(octx)
        codec_lib.av_packet_free(byref(packet))
        ffmpeg.close_input(ictx)


def _origin(streams) -> float:
    """Time zero: the first video frame (Qt's own MKVs, for one, start their streams at about 1 s)."""
    for stream in streams:
        if stream.codecpar.contents.codec_type == ffmpeg.AVMEDIA_TYPE_VIDEO:
            return max(0.0, ffmpeg.seconds(stream.start_time, stream.time_base) or 0.0)
    return 0.0


def _copy_packets(ictx, octx, packet, streams, bases, out_bases, first: float, last: float | None) -> None:
    libs = ffmpeg.libs()
    fmt, codec_lib = libs["avformat"], libs["avcodec"]
    finished = [False] * len(streams)
    while not all(finished):
        code = fmt.av_read_frame(ictx, packet)
        if code == AVERROR_EOF:
            return
        if code < 0:
            raise ffmpeg.FFmpegError(f"Couldn't read the video: {ffmpeg.message(code)}")
        try:
            p = packet.contents
            i = p.stream_index
            base = bases[i]
            stamp = p.pts if p.pts != ffmpeg.AV_NOPTS_VALUE else p.dts
            t = stamp * base.num / base.den
            if last is not None and t >= last - EPSILON:
                finished[i] = True
                continue
            audio = streams[i].codecpar.contents.codec_type == ffmpeg.AVMEDIA_TYPE_AUDIO
            if audio and t < first - AUDIO_PREROLL:
                continue
            shift = round(first * base.den / base.num)
            if p.pts != ffmpeg.AV_NOPTS_VALUE:
                p.pts -= shift
            if p.dts != ffmpeg.AV_NOPTS_VALUE:
                p.dts -= shift  # before zero ahead of the cut: the MP4 writer adds an edit list
            p.pos = -1
            codec_lib.av_packet_rescale_ts(packet, base, out_bases[i])
            code = fmt.av_interleaved_write_frame(octx, packet)
            if code < 0:
                raise ffmpeg.FFmpegError(f"Couldn't write the video: {ffmpeg.message(code)}")
        finally:
            codec_lib.av_packet_unref(packet)
