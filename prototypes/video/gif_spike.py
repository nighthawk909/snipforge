"""Spike: MP4 -> animated GIF. Frames are decoded with Qt's own FFmpeg (ctypes, as in trim_spike),
scaled by swscale, and written by Pillow (palette per frame, Floyd-Steinberg dither, only the
changed part of each frame stored).

    python prototypes/video/gif_spike.py in.mp4 out.gif [--fps 12] [--width 960] [--start 0] [--end 999]
"""
from __future__ import annotations

import argparse
import ctypes
import time
from ctypes import POINTER, byref, c_char_p, c_int, c_uint, c_void_p

from PIL import Image
from trim_spike import (
    AV_TIME_BASE,
    AVERROR_EOF,
    AVSEEK_FLAG_BACKWARD,
    FMT_NB_STREAMS,
    FMT_STREAMS,
    QT,
    ST_CODECPAR,
    ST_TIME_BASE,
    AVRational,
    _check,
    _field,
    avcodec,
    avformat,
    avutil,
)

swscale = ctypes.CDLL(f"{QT}/swscale-10.dll")
EAGAIN = -11
AVMEDIA_TYPE_VIDEO = 0
PAR_CODEC_ID = 4
FRAME_DATA, FRAME_LINESIZE, FRAME_WIDTH, FRAME_HEIGHT, FRAME_FORMAT = 0, 64, 104, 108, 116
SWS_AREA = 0x20

for name, res, args in [
    ("avcodec_find_decoder", c_void_p, [c_int]),
    ("avcodec_alloc_context3", c_void_p, [c_void_p]),
    ("avcodec_parameters_to_context", c_int, [c_void_p, c_void_p]),
    ("avcodec_open2", c_int, [c_void_p, c_void_p, c_void_p]),
    ("avcodec_send_packet", c_int, [c_void_p, c_void_p]),
    ("avcodec_receive_frame", c_int, [c_void_p, c_void_p]),
    ("avcodec_free_context", None, [POINTER(c_void_p)]),
]:
    fn = getattr(avcodec, name)
    fn.restype, fn.argtypes = res, args
for name, res, args in [
    ("av_frame_alloc", c_void_p, []),
    ("av_frame_free", None, [POINTER(c_void_p)]),
    ("av_frame_unref", None, [c_void_p]),
    ("av_get_pix_fmt", c_int, [c_char_p]),
]:
    fn = getattr(avutil, name)
    fn.restype, fn.argtypes = res, args
swscale.sws_getContext.restype = c_void_p
swscale.sws_getContext.argtypes = [c_int, c_int, c_int, c_int, c_int, c_int, c_int, c_void_p, c_void_p, c_void_p]
swscale.sws_scale.argtypes = [c_void_p, c_void_p, c_void_p, c_int, c_int, c_void_p, c_void_p]
swscale.sws_freeContext.argtypes = [c_void_p]
BGRA = avutil.av_get_pix_fmt(b"bgra")


def frames(src: str, fps: float, width: int, start: float, end: float):
    """Yield (seconds, PIL RGB image) at `fps`, scaled to `width`."""
    ictx = c_void_p()
    _check(avformat.avformat_open_input(byref(ictx), src.encode(), None, None), "open")
    dec = c_void_p()
    frame = c_void_p(avutil.av_frame_alloc())
    pkt = avcodec.av_packet_alloc()
    sws = None
    try:
        _check(avformat.avformat_find_stream_info(ictx, None), "stream info")
        count = _field(ictx.value, FMT_NB_STREAMS, c_uint).value
        streams = ctypes.cast(_field(ictx.value, FMT_STREAMS, c_void_p).value, POINTER(c_void_p))
        vi = next(i for i in range(count)
                  if _field(_field(streams[i], ST_CODECPAR, c_void_p).value, 0, c_int).value == AVMEDIA_TYPE_VIDEO)
        par = _field(streams[vi], ST_CODECPAR, c_void_p).value
        tb = AVRational.from_address(streams[vi] + ST_TIME_BASE)
        codec = avcodec.avcodec_find_decoder(_field(par, PAR_CODEC_ID, c_int).value)
        dec = c_void_p(avcodec.avcodec_alloc_context3(codec))
        _check(avcodec.avcodec_parameters_to_context(dec, par), "decoder parameters")
        _check(avcodec.avcodec_open2(dec, codec, None), "open decoder")
        if start > 0:
            _check(avformat.av_seek_frame(ictx, -1, int(start * AV_TIME_BASE), AVSEEK_FLAG_BACKWARD), "seek")
        pts_queue: list[float] = []  # Baseline H.264 has no B-frames, so frames come out in packet order
        next_time = start
        out_w = out_h = 0
        dst = (c_void_p * 4)()
        dst_stride = (c_int * 4)()
        buffer = None
        eof = False
        while True:
            if not eof:
                code = avformat.av_read_frame(ictx, pkt)
                if code == AVERROR_EOF:
                    eof = True
                    _check(avcodec.avcodec_send_packet(dec, None), "flush")
                else:
                    _check(code, "read")
                    if pkt.contents.stream_index != vi:
                        avcodec.av_packet_unref(pkt)
                        continue
                    pts_queue.append(pkt.contents.pts * tb.num / tb.den)
                    _check(avcodec.avcodec_send_packet(dec, pkt), "decode")
                    avcodec.av_packet_unref(pkt)
            while True:
                code = avcodec.avcodec_receive_frame(dec, frame)
                if code in (EAGAIN, AVERROR_EOF):
                    break
                _check(code, "receive frame")
                t = pts_queue.pop(0) if pts_queue else next_time
                f = frame.value
                if t >= end:
                    return
                if t + 1e-6 >= next_time:
                    w, h = _field(f, FRAME_WIDTH, c_int).value, _field(f, FRAME_HEIGHT, c_int).value
                    if sws is None:
                        out_w = min(width, w) // 2 * 2
                        out_h = round(h * out_w / w) // 2 * 2
                        sws = swscale.sws_getContext(w, h, _field(f, FRAME_FORMAT, c_int).value, out_w, out_h, BGRA,
                                                     SWS_AREA, None, None, None)
                        buffer = ctypes.create_string_buffer(out_w * out_h * 4)
                        dst[0] = ctypes.addressof(buffer)
                        dst_stride[0] = out_w * 4
                    swscale.sws_scale(sws, f + FRAME_DATA, f + FRAME_LINESIZE, 0, h, dst, dst_stride)
                    yield t, Image.frombuffer("RGBA", (out_w, out_h), buffer.raw, "raw", "BGRA", 0, 1).convert("RGB")
                    while next_time <= t + 1e-6:
                        next_time += 1 / fps
                avutil.av_frame_unref(frame)
            if eof:
                return
    finally:
        if sws:
            swscale.sws_freeContext(sws)
        avutil.av_frame_free(byref(frame))
        avcodec.av_packet_free(byref(ctypes.cast(pkt, c_void_p)))
        if dec:
            avcodec.avcodec_free_context(byref(dec))
        avformat.avformat_close_input(byref(ictx))


def make_gif(src: str, dst: str, fps: float, width: int, start: float = 0.0, end: float = 1e9) -> dict:
    t0 = time.perf_counter()
    images = []
    for _t, image in frames(src, fps, width, start, end):
        images.append(image.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.FLOYDSTEINBERG))
    t1 = time.perf_counter()
    images[0].save(dst, save_all=True, append_images=images[1:], duration=round(1000 / fps), loop=0, optimize=False,
                   disposal=1)
    t2 = time.perf_counter()
    return {"frames": len(images), "decode_quantize_s": round(t1 - t0, 2), "write_s": round(t2 - t1, 2)}


if __name__ == "__main__":
    import os
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--fps", type=float, default=12)
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=1e9)
    a = ap.parse_args()
    info = make_gif(a.src, a.dst, a.fps, a.width, a.start, a.end)
    print(f"{info}, {os.path.getsize(a.dst) / 1e6:.2f} MB")
