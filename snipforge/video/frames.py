"""Reading a video's frames as images, with the FFmpeg libraries Qt ships: for GIF export and the
editor's timeline. Exact and as fast as the machine decodes, unlike Qt's player, which plays in real
time and skips frames when it falls behind.

Colours are converted with FFmpeg's defaults (BT.601, limited range), which is what Qt's encoder
writes; files from elsewhere tagged otherwise may come out slightly off.
"""
from __future__ import annotations

import contextlib
import ctypes
import logging
import os
from collections.abc import Iterator
from ctypes import POINTER, byref, c_int, c_void_p

from PySide6.QtGui import QImage

from . import ffmpeg

log = logging.getLogger(__name__)

SWS_AREA = 0x20  # good for shrinking; exact when the size doesn't change
EPSILON = 1e-6


def read(path: str | os.PathLike, fps: float | None = None, width: int | None = None, start: float = 0.0,
         end: float | None = None) -> Iterator[tuple[float, QImage]]:
    """(seconds, image) for the frames shown from `start` up to `end`. With `fps`, only the first
    frame at or after each 1/fps step; with `width`, shrunk to that width (never enlarged)."""
    if fps is not None and not fps > 0:
        raise ValueError(f"fps must be more than 0, not {fps}")
    if width is not None and width < 1:
        raise ValueError(f"width must be at least 1, not {width}")
    with contextlib.ExitStack() as cleanup:
        ctx = ffmpeg.open_input(path)
        cleanup.callback(ffmpeg.close_input, ctx)
        stream = ffmpeg.video_stream(ctx)
        if stream is None:
            raise ffmpeg.FFmpegError(f"{os.path.basename(os.fspath(path))} has no video")
        origin = max(0.0, ffmpeg.seconds(stream.start_time, stream.time_base) or 0.0)  # Qt's MKVs start late
        _seek(ctx, stream, start + origin)
        scaler = cleanup.enter_context(_Scaler(width))
        decoded = cleanup.enter_context(contextlib.closing(_decoded_frames(ctx, stream, os.fspath(path))))
        next_time = start
        for frame in decoded:
            t = (ffmpeg.seconds(frame.best_effort_timestamp, stream.time_base) or 0.0) - origin
            if end is not None and t >= end - EPSILON:
                return
            if t < next_time - EPSILON:
                continue
            yield t, scaler.image(frame)
            next_time = _next_step(next_time, t, fps)


def _next_step(previous: float, shown: float, fps: float | None) -> float:
    """The next time a frame is wanted: the first 1/fps step after the one just shown (so the rate
    holds even when it doesn't divide the video's), or any later frame without fps."""
    if not fps:
        return shown + EPSILON
    step = 1 / fps
    following = previous + step
    while following <= shown + EPSILON:
        following += step
    return following


def _seek(ctx, stream: ffmpeg.AVStream, at: float) -> None:
    if at <= EPSILON:
        return
    base = stream.time_base
    code = ffmpeg.libs()["avformat"].av_seek_frame(ctx, stream.index, round(at * base.den / base.num),
                                                   ffmpeg.AVSEEK_FLAG_BACKWARD)
    if code < 0:
        raise ffmpeg.FFmpegError(f"Couldn't go to {at:.2f} s: {ffmpeg.message(code)}")


class _Scaler:
    """Turns decoded frames into BGRA QImages (QImage.Format_ARGB32), reusing swscale's setup."""

    def __init__(self, width: int | None) -> None:
        self.width = width
        self.context = None
        self.key: tuple = ()
        self.buffer = None

    def __enter__(self) -> _Scaler:
        return self

    def __exit__(self, *_exc) -> None:
        if self.context:
            ffmpeg.libs()["swscale"].sws_freeContext(self.context)
            self.context = None

    def image(self, frame: ffmpeg.AVFrame) -> QImage:
        sws = ffmpeg.libs()["swscale"]
        w, h = frame.width, frame.height
        out_w = min(self.width, w) if self.width else w
        out_h = max(1, round(h * out_w / w))
        if self.key != (w, h, frame.format, out_w, out_h):
            self.__exit__()
            bgra = ffmpeg.libs()["avutil"].av_get_pix_fmt(b"bgra")
            self.context = sws.sws_getContext(w, h, frame.format, out_w, out_h, bgra, SWS_AREA, None, None, None)
            if not self.context:
                raise ffmpeg.FFmpegError(f"Can't convert {w}x{h} video frames")
            self.key = (w, h, frame.format, out_w, out_h)
            self.buffer = ctypes.create_string_buffer(out_w * out_h * 4)
        planes = (c_void_p * 4)(ctypes.addressof(self.buffer))
        strides = (c_int * 4)(out_w * 4)
        sws.sws_scale(self.context, ctypes.cast(frame.data, c_void_p), ctypes.cast(frame.linesize, c_void_p), 0, h,
                      planes, strides)
        return QImage(self.buffer.raw, out_w, out_h, out_w * 4, QImage.Format.Format_ARGB32).copy()


def _open_decoder(stream: ffmpeg.AVStream, name: str) -> c_void_p:
    codec_lib = ffmpeg.libs()["avcodec"]
    codec = codec_lib.avcodec_find_decoder(stream.codecpar.contents.codec_id)
    if not codec:
        raise ffmpeg.FFmpegError(f"No decoder for the video in {name}")
    decoder = c_void_p(codec_lib.avcodec_alloc_context3(codec))
    code = codec_lib.avcodec_parameters_to_context(decoder, stream.codecpar)
    if code >= 0:
        code = codec_lib.avcodec_open2(decoder, codec, None)
    if code < 0:
        codec_lib.avcodec_free_context(byref(decoder))
        raise ffmpeg.FFmpegError(f"Couldn't decode the video in {name}: {ffmpeg.message(code)}")
    return decoder


def _decoded_frames(ctx, stream: ffmpeg.AVStream, name: str) -> Iterator[ffmpeg.AVFrame]:
    """Every decoded frame of `stream`, in display order; each is valid until the next. A damaged
    packet is skipped (logged), as players do, rather than ending the whole read."""
    libs = ffmpeg.libs()
    fmt, codec_lib, util = libs["avformat"], libs["avcodec"], libs["avutil"]
    decoder = _open_decoder(stream, name)
    frame = util.av_frame_alloc()
    packet = codec_lib.av_packet_alloc()
    try:
        done = False
        while not done:
            code = fmt.av_read_frame(ctx, packet)
            if code == ffmpeg.AVERROR_EOF:
                codec_lib.avcodec_send_packet(decoder, POINTER(ffmpeg.AVPacket)())  # flush the frames still inside
                done = True
            elif code < 0:
                raise ffmpeg.FFmpegError(f"Couldn't read {name}: {ffmpeg.message(code)}")
            elif packet.contents.stream_index == stream.index:
                code = codec_lib.avcodec_send_packet(decoder, packet)
                if code == ffmpeg.AVERROR_INVALIDDATA:
                    log.warning("Skipped a damaged video packet in %s", name)
                elif code < 0 and code != ffmpeg.AVERROR_EAGAIN:
                    raise ffmpeg.FFmpegError(f"Couldn't decode {name}: {ffmpeg.message(code)}")
            codec_lib.av_packet_unref(packet)
            while (code := codec_lib.avcodec_receive_frame(decoder, frame)) >= 0:
                try:
                    yield frame.contents
                finally:
                    util.av_frame_unref(frame)
            if code not in (ffmpeg.AVERROR_EAGAIN, ffmpeg.AVERROR_EOF, ffmpeg.AVERROR_INVALIDDATA):
                raise ffmpeg.FFmpegError(f"Couldn't decode {name}: {ffmpeg.message(code)}")
    finally:
        util.av_frame_free(byref(frame))
        codec_lib.av_packet_free(byref(packet))
        codec_lib.avcodec_free_context(byref(decoder))
