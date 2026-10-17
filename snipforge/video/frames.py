"""Reading a video's frames as images, with the FFmpeg libraries Qt ships: for GIF export and the
editor's timeline. Exact and as fast as the machine decodes, unlike Qt's player, which plays in real
time and skips frames when it falls behind.
"""
from __future__ import annotations

import ctypes
import os
from collections.abc import Iterator
from ctypes import POINTER, byref, c_int, c_void_p

from PySide6.QtGui import QImage

from . import ffmpeg

AVERROR_EOF = -0x20464F45  # FFERRTAG('E', 'O', 'F', ' ')
AVERROR_EAGAIN = -11
AVSEEK_FLAG_BACKWARD = 1
SWS_AREA = 0x20  # good for shrinking; exact when the size doesn't change
EPSILON = 1e-6


def read(path: str | os.PathLike, fps: float | None = None, width: int | None = None, start: float = 0.0,
         end: float | None = None) -> Iterator[tuple[float, QImage]]:
    """(seconds, image) for each frame shown from `start` up to `end`, at most `fps` a second (the
    first frame at or after each 1/fps step), scaled to `width` (height in proportion)."""
    libs = ffmpeg.libs()
    codec_lib, util, sws = libs["avcodec"], libs["avutil"], libs["swscale"]
    ctx = ffmpeg.open_input(path)
    decoder = c_void_p()
    frame = util.av_frame_alloc()
    packet = codec_lib.av_packet_alloc()
    scaler = None
    try:
        fc = ctx.contents
        stream = next((fc.streams[i].contents for i in range(fc.nb_streams)
                       if fc.streams[i].contents.codecpar.contents.codec_type == ffmpeg.AVMEDIA_TYPE_VIDEO), None)
        if stream is None:
            raise ffmpeg.FFmpegError(f"{os.fspath(path)} has no video")
        decoder = _open_decoder(stream, os.fspath(path))
        base = stream.time_base
        origin = ffmpeg.seconds(stream.start_time, base) or 0.0
        origin = max(origin, 0.0)  # a file whose video starts late (Qt's own MKVs) counts from its first frame
        if start > 0:
            ffmpeg.libs()["avformat"].av_seek_frame(ctx, stream.index, round((start + origin) * base.den / base.num),
                                                    AVSEEK_FLAG_BACKWARD)
        step = 1 / fps if fps else 0.0
        next_time = start
        bgra = util.av_get_pix_fmt(b"bgra")
        scaled = (0, 0, -1, 0, 0)
        buffer = None
        for decoded in _decoded_frames(ctx, decoder, packet, frame, stream.index):
            t = (ffmpeg.seconds(decoded.best_effort_timestamp, base) or 0.0) - origin
            if end is not None and t >= end - EPSILON:
                return
            if t < next_time - EPSILON:
                continue
            w, h = decoded.width, decoded.height
            out_w = min(width or w, w) // 2 * 2
            out_h = max(2, round(h * out_w / w) // 2 * 2)
            if scaled != (w, h, decoded.format, out_w, out_h):
                if scaler:
                    sws.sws_freeContext(scaler)
                scaler = sws.sws_getContext(w, h, decoded.format, out_w, out_h, bgra, SWS_AREA, None, None, None)
                if not scaler:
                    raise ffmpeg.FFmpegError(f"Can't convert {w}x{h} frames in {os.fspath(path)}")
                scaled = (w, h, decoded.format, out_w, out_h)
                buffer = ctypes.create_string_buffer(out_w * out_h * 4)
            planes = (c_void_p * 4)(ctypes.addressof(buffer))
            strides = (c_int * 4)(out_w * 4)
            sws.sws_scale(scaler, ctypes.cast(decoded.data, c_void_p), ctypes.cast(decoded.linesize, c_void_p), 0, h,
                          planes, strides)
            image = QImage(buffer.raw, out_w, out_h, out_w * 4, QImage.Format.Format_ARGB32).copy()
            yield t, image
            next_time = t + step if step else t + EPSILON
            while step and next_time <= t + EPSILON:
                next_time += step
    finally:
        if scaler:
            sws.sws_freeContext(scaler)
        util.av_frame_free(byref(frame))
        codec_lib.av_packet_free(byref(packet))
        if decoder:
            codec_lib.avcodec_free_context(byref(decoder))
        ffmpeg.close_input(ctx)


def _open_decoder(stream, name: str) -> c_void_p:
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


def _decoded_frames(ctx, decoder, packet, frame, index: int) -> Iterator[ffmpeg.AVFrame]:
    """Every decoded frame of stream `index`, in display order (valid until the next one)."""
    libs = ffmpeg.libs()
    fmt, codec_lib, util = libs["avformat"], libs["avcodec"], libs["avutil"]
    done = False
    while not done:
        code = fmt.av_read_frame(ctx, packet)
        if code == AVERROR_EOF:
            codec_lib.avcodec_send_packet(decoder, POINTER(ffmpeg.AVPacket)())  # flush: frames still inside
            done = True
        elif code < 0:
            raise ffmpeg.FFmpegError(f"Couldn't read the video: {ffmpeg.message(code)}")
        else:
            try:
                if packet.contents.stream_index != index:
                    continue
                code = codec_lib.avcodec_send_packet(decoder, packet)
                if code < 0 and code != AVERROR_EAGAIN:
                    raise ffmpeg.FFmpegError(f"Couldn't decode the video: {ffmpeg.message(code)}")
            finally:
                codec_lib.av_packet_unref(packet)
        while True:
            code = codec_lib.avcodec_receive_frame(decoder, frame)
            if code in (AVERROR_EAGAIN, AVERROR_EOF):
                break
            if code < 0:
                raise ffmpeg.FFmpegError(f"Couldn't decode the video: {ffmpeg.message(code)}")
            try:
                yield frame.contents
            finally:
                util.av_frame_unref(frame)
